import pytest
from bot.services.urls import normalize_url
from bot.services.extraction import metadata_from_ytdlp
from bot.models import VideoFormat
from bot.services.quality import select_formats
from bot.models.exceptions import VideoUnavailableError

URL = 'https://www.tiktok.com/@author/photo/123'


def test_photo_url_is_accepted():
    assert normalize_url(URL + '?tracking=1') == URL


def test_photo_metadata_preserves_order_and_rejects_incomplete_album():
    data = {'id': '123', 'media_type': 'photo', 'photos': [
        {'urls': ['https://cdn.example/2.jpg'], 'width': 2000, 'height': 3000},
        {'urls': ['https://cdn.example/1.jpg'], 'width': 1000, 'height': 1500},
    ]}
    info = metadata_from_ytdlp(data, URL)
    assert info.media_type == 'photo'
    assert [str(p.urls[0]) for p in info.photos] == ['https://cdn.example/2.jpg', 'https://cdn.example/1.jpg']
    data['photos'].append({'urls': []})
    with pytest.raises(VideoUnavailableError):
        metadata_from_ytdlp(data, URL)


def test_maximum_and_clean_are_independent():
    formats = [VideoFormat(format_id='clean', ext='mp4', width=720, height=1280, has_watermark=False),
               VideoFormat(format_id='max', ext='mp4', width=2160, height=3840, has_watermark=True)]
    assert select_formats(formats, preference='maximum').video.format_id == 'clean'
    assert select_formats(formats, preference='clean').video.format_id == 'clean'
    with pytest.raises(VideoUnavailableError):
        select_formats(formats[1:], preference='clean')


def test_public_item_original_image_variant_and_playback_evidence():
    from bot.services.tiktok_sources import photo_metadata, annotate_video_sources
    item = {'id': '123', 'imagePost': {'images': [
        {'displayImage': {'urlList': ['https://cdn.example/small'], 'width': 600, 'height': 900},
         'ownerWatermarkImage': {'urlList': ['https://cdn.example/wm'], 'width': 3000, 'height': 4500},
         'originImage': {'urlList': ['https://cdn.example/original'], 'width': 2000, 'height': 3000}}
    ]}, 'video': {'playAddr': 'https://cdn.example/play', 'downloadAddr': 'https://cdn.example/download'}}
    photo = photo_metadata(item, URL)['photos'][0]
    assert photo['urls'] == ['https://cdn.example/original']
    assert photo['width'] == 2000
    assert photo['has_watermark'] is None  # "original" is not visual verification
    info = {'formats': [{'url': 'https://cdn.example/play'}, {'url': 'https://cdn.example/download'},
                        {'url': 'https://cdn.example/unknown'}]}
    annotate_video_sources(info, item)
    assert info['formats'][0]['has_watermark'] is False
    assert 'playback' in info['formats'][0]['watermark_evidence']
    assert info['formats'][1]['has_watermark'] is True
    assert 'has_watermark' not in info['formats'][2]


@pytest.mark.asyncio
async def test_photo_download_zip_preserves_bytes_order_and_cleans(tmp_path, monkeypatch):
    import json
    import zipfile
    from bot.services import photos
    from bot.models import VideoInfo, PhotoInfo, VideoFormat
    from bot.services.extraction import Extraction
    from bot.config import Settings
    info = VideoInfo(id='123', webpage_url=URL, media_type='photo', photos=[
        PhotoInfo(has_watermark=False, urls=['https://cdn.example/1']), PhotoInfo(has_watermark=False, urls=['https://cdn.example/2'])])
    metadata = tmp_path / 'info.json'
    metadata.write_text('{}')

    async def worker(action, payload, timeout):
        data = json.loads(payload.read_text())
        for index, value in enumerate([b'first-original', b'second-original'], 1):
            (photos.Path(data['media_dir']) / f'{index:04d}.img').write_bytes(value)

    async def verify(path, **kwargs):
        return VideoFormat(has_watermark=False, format_id='probe', ext='img', width=2000, height=3000, video_codec='mjpeg')

    monkeypatch.setattr(photos, 'run_worker', worker)
    monkeypatch.setattr(photos, 'verify_media', verify)
    workspace = tmp_path / 'work'
    workspace.mkdir()
    result = await photos.download_photos(Extraction(info, metadata), workspace, Settings(DOWNLOAD_DIR=tmp_path), 10)
    with zipfile.ZipFile(result.file_path) as archive:
        assert archive.namelist() == ['0001.jpg', '0002.jpg']
        assert archive.read('0001.jpg') == b'first-original'
    assert [p.width for p in result.photos] == [2000, 2000]
    directory = result.file_path.parent
    result.cleanup()
    assert not directory.exists()


def test_dynamic_actions_hide_unavailable_clean_and_detect_photos():
    from bot.handlers.keyboards import download_keyboard
    from bot.models import VideoInfo, PhotoInfo
    video = VideoInfo(id='123', webpage_url=URL, formats=[VideoFormat(has_watermark=False, format_id='a', ext='mp4')])
    modes = lambda info: [b.callback_data.split(':')[1] for row in download_keyboard('key', info).inline_keyboard for b in row if b.callback_data.startswith('dl:')]
    assert modes(video) == ['chat', 'file', 'zip', 'audio']
    video.formats[0].has_watermark = False
    assert modes(video) == ['chat', 'file', 'zip', 'audio']
    photo = VideoInfo(id='123', webpage_url=URL, media_type='photo', photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/1'])])
    assert modes(photo) == ['chat', 'file', 'zip']


@pytest.mark.asyncio
@pytest.mark.parametrize('count,groups,singles', [(1, [], 1), (10, [10], 0), (11, [10], 1), (23, [10, 10, 3], 0)])
async def test_photo_album_batches_keep_order(tmp_path, count, groups, singles):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from bot.models import VideoInfo, PhotoInfo, PhotoFile, DownloadResult
    from bot.services.telegram_sender import TelegramSender
    paths = []
    for i in range(count):
        path = tmp_path / f'{i:04d}.jpg'
        path.write_bytes(b'image')
        paths.append(PhotoFile(file_path=path, width=100, height=200, size=5))
    archive = tmp_path / 'originals.zip'
    archive.write_bytes(b'zip')
    result = DownloadResult(video_info=VideoInfo(id='123', webpage_url=URL, media_type='photo',
        photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/1']) for _ in paths]),
        file_path=archive, file_size_bytes=3, photos=paths)
    bot = SimpleNamespace(send_media_group=AsyncMock(return_value=[]), send_photo=AsyncMock())
    await TelegramSender().send(bot, 1, result, 'album')
    assert [len(call.kwargs['media']) for call in bot.send_media_group.call_args_list] == groups
    assert bot.send_photo.await_count == singles
    sent = [m.media.path.name for c in bot.send_media_group.call_args_list for m in c.kwargs['media']]
    sent += [c.kwargs['photo'].path.name for c in bot.send_photo.call_args_list]
    assert sent == [p.file_path.name for p in paths]
    captions = [m.caption for c in bot.send_media_group.call_args_list for m in c.kwargs['media']]
    captions += [c.kwargs['caption'] for c in bot.send_photo.call_args_list]
    assert all('100×200' in caption for caption in captions)


def test_cache_separates_clean_and_maximum_selection():
    from bot.models import VideoInfo
    from bot.services.runtime import quality_key
    info = VideoInfo(id='123', webpage_url=URL, formats=[
        VideoFormat(format_id='clean', ext='mp4', width=720, height=1280, has_watermark=False),
        VideoFormat(format_id='maximum', ext='mp4', width=2160, height=3840, has_watermark=True)])
    assert quality_key(info, 'clean') == quality_key(info, 'maximum')
    assert quality_key(info, 'document') == quality_key(info, 'maximum')


def test_local_api_endpoint_changes_session_and_upload_limit():
    from bot.config import Settings
    from bot.telegram_config import create_bot, create_dispatcher
    settings = Settings(BOT_TOKEN='123456:TEST_ONLY_NOT_A_REAL_TOKEN', TELEGRAM_API_BASE='http://localhost:8081',
                        MAX_FILE_SIZE_MB=1900)
    bot = create_bot(settings)
    assert bot.session.api.is_local
    assert bot.session.api.api_url(bot.token, 'getMe').startswith('http://localhost:8081/')
    dispatcher = create_dispatcher(settings)
    sender = dispatcher['sender']
    sender.check_size(1_900_000_000)
    from bot.models.exceptions import FileSizeLimitError
    with pytest.raises(FileSizeLimitError):
        sender.check_size(2_000_000_001)


@pytest.mark.parametrize('endpoint', ['file:///etc/passwd', 'http://user:pass@localhost:8081', 'https://host/?secret=x'])
def test_local_api_rejects_invalid_endpoints(endpoint):
    from pydantic import ValidationError
    from bot.config import Settings
    with pytest.raises(ValidationError):
        Settings(TELEGRAM_API_BASE=endpoint)


def test_web_photo_image_url_schema_keeps_all_mirrors():
    from bot.services.tiktok_sources import photo_metadata
    data = photo_metadata({'id': '123', 'imagePost': {'images': [{
        'imageURL': {'urlList': ['https://cdn.example/first', 'https://cdn.example/mirror']},
        'imageWidth': 1399, 'imageHeight': 1080}]}}, URL)
    assert data['photos'][0]['urls'] == ['https://cdn.example/first', 'https://cdn.example/mirror']
    assert data['photos'][0]['width'] == 1399


@pytest.mark.asyncio
async def test_zip_upload_and_incompatible_preview_use_original_documents(tmp_path):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from bot.models import PhotoInfo, PhotoFile, VideoInfo, DownloadResult
    from bot.services.telegram_sender import TelegramSender
    path = tmp_path / '0001.webp'
    path.write_bytes(b'original-image')
    archive = tmp_path / 'originals.zip'
    archive.write_bytes(b'archive')
    result = DownloadResult(file_path=archive, file_size_bytes=7,
        video_info=VideoInfo(id='123', webpage_url=URL, media_type='photo', photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/1'])]),
        photos=[PhotoFile(file_path=path, width=2000, height=3000, size=14)])
    bot = SimpleNamespace(send_document=AsyncMock(), send_photo=AsyncMock())
    await TelegramSender().send(bot, 1, result, 'zip')
    assert bot.send_document.call_args.kwargs['document'].path == archive
    await TelegramSender().send(bot, 1, result, 'album')
    assert bot.send_document.call_args.kwargs['document'].path == path
    bot.send_photo.assert_not_awaited()


def test_worker_handles_real_photo_schema_before_video_processing(monkeypatch):
    from bot.services import ytdlp_worker
    item = {'id': '123', 'imagePost': {'images': [{'imageURL': {'urlList': ['https://cdn.example/1']}}]}}
    monkeypatch.setattr(ytdlp_worker.TikTokIE, '_extract_web_data_and_status', lambda *args: (item, 0))
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        info = ytdlp_worker.extract_job(URL, ydl)
    assert info['media_type'] == 'photo'
    assert len(info['photos']) == 1


def test_worker_photo_network_mirror_and_stream_size_limit(tmp_path, monkeypatch):
    import io
    from bot.services import ytdlp_worker
    attempts = []

    def fetch(self, request):
        attempts.append(request.url)
        if 'bad' in request.url:
            raise OSError('connection reset')
        return io.BytesIO(b'original-image')

    monkeypatch.setattr(ytdlp_worker.YoutubeDL, 'urlopen', fetch)
    job = {'photos': [{'urls': ['https://cdn.example/bad', 'https://cdn.example/good']}],
           'url': URL, 'media_dir': str(tmp_path), 'max_bytes': 100}
    ytdlp_worker.photo_download_job(job)
    assert (tmp_path / '0001.img').read_bytes() == b'original-image'
    assert attempts == ['https://cdn.example/bad', 'https://cdn.example/good']
    job['max_bytes'] = 3
    with pytest.raises(ValueError, match='size limit'):
        ytdlp_worker.photo_download_job(job)
    assert not (tmp_path / '0001.img').exists()


@pytest.mark.asyncio
async def test_photo_pipeline_real_image_probe_and_decode(tmp_path, monkeypatch):
    import binascii
    import struct
    import zlib
    import json
    from bot.services import photos
    from bot.models import VideoInfo, PhotoInfo
    from bot.config import Settings
    from bot.services.extraction import Extraction

    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', binascii.crc32(kind + data) & 0xffffffff)

    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 2, 3, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff\x00\x00' * 2) * 3)) + chunk(b'IEND', b'')

    async def worker(action, payload, timeout):
        data = json.loads(payload.read_text())
        (photos.Path(data['media_dir']) / '0001.img').write_bytes(png)

    monkeypatch.setattr(photos, 'run_worker', worker)
    info = VideoInfo(id='123', webpage_url=URL, media_type='photo', photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/a'])])
    work = tmp_path / 'work'
    work.mkdir()
    result = await photos.download_photos(Extraction(info, work / 'info.json'), work, Settings(DOWNLOAD_DIR=tmp_path), 10)
    assert (result.photos[0].width, result.photos[0].height) == (2, 3)
    assert result.photos[0].file_path.read_bytes() == png
    result.cleanup()


def test_buttons_keep_smaller_clean_variant_when_maximum_is_too_large():
    from bot.handlers.keyboards import download_keyboard
    from bot.models import VideoInfo
    info = VideoInfo(id='123', webpage_url=URL, formats=[
        VideoFormat(format_id='clean', ext='mp4', has_watermark=False, filesize=20_000_000),
        VideoFormat(has_watermark=True, format_id='max', ext='mp4', width=2160, height=3840, filesize=80_000_000)])
    from bot.models.preferences import UserPreferences
    keyboard = download_keyboard('key', info, max_bytes=50_000_000,
                                 preferences=UserPreferences(watermark='prefer_clean'))
    assert [r[0].callback_data.split(':')[1] for r in keyboard.inline_keyboard if r[0].callback_data.startswith('dl:')] == ['chat', 'file', 'zip', 'audio']


def test_photo_link_without_images_must_not_become_audio_video(monkeypatch):
    from bot.services import ytdlp_worker
    item = {'id': '123', 'video': {}, 'music': {'playUrl': 'https://cdn.example/music'}}
    monkeypatch.setattr(ytdlp_worker.TikTokIE, '_extract_web_data_and_status', lambda *a: (item, 0))
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        with pytest.raises(ValueError, match='photo'):
            ytdlp_worker.extract_job(URL, ydl)


@pytest.mark.asyncio
@pytest.mark.parametrize('actual,mode', [('photo', 'maximum'), ('photo', 'document'), ('video', 'album')])
async def test_changed_post_kind_never_sends_wrong_artifact(tmp_path, actual, mode):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from bot.models import VideoInfo, PhotoInfo, DownloadResult
    from bot.services.telegram_sender import TelegramSender
    from bot.models.exceptions import TelegramDeliveryError
    info = VideoInfo(id='123', webpage_url=URL, media_type=actual,
                     photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/1'])] if actual == 'photo' else [])
    path = tmp_path / 'original.zip'
    path.write_bytes(b'data')
    result = DownloadResult(video_info=info, file_path=path, file_size_bytes=4)
    bot = SimpleNamespace(send_document=AsyncMock())
    with pytest.raises(TelegramDeliveryError):
        await TelegramSender().send(bot, 1, result, mode)
    bot.send_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_photo_recipients_share_artifact_and_cleanup(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from bot.config import Settings
    from bot.models import VideoInfo, PhotoInfo, PhotoFile, DownloadResult
    from bot.services.runtime import BotRuntime
    from bot.services.telegram_sender import TelegramSender
    runtime = BotRuntime(Settings(DOWNLOAD_DIR=tmp_path, CACHE_ENABLED=False, MAX_CONCURRENT_DOWNLOADS=2))
    info = VideoInfo(id='123', webpage_url=URL, media_type='photo', photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/1'])])
    both = asyncio.Event()
    calls = []
    artifacts = []

    async def download(url):
        folder = runtime.work_dir / 'tiktok-published'
        folder.mkdir()
        image, archive = folder / '0001.jpg', folder / 'originals.zip'
        image.write_bytes(b'image')
        archive.write_bytes(b'zip')
        artifacts.extend([image, archive])
        return DownloadResult(video_info=info, file_path=archive, file_size_bytes=3,
                              photos=[PhotoFile(file_path=image, width=2, height=3, size=5)])

    async def send(**kwargs):
        calls.append(kwargs['chat_id'])
        if len(calls) == 2:
            both.set()
        await both.wait()
        assert all(p.exists() for p in artifacts)

    bot = SimpleNamespace(send_document=AsyncMock(side_effect=send))
    service = SimpleNamespace(download=AsyncMock(side_effect=download))
    progress = SimpleNamespace(update=AsyncMock())
    try:
        await runtime.start()
        await asyncio.wait_for(asyncio.gather(*[
            runtime.deliver(bot, chat, info, 'zip', service, TelegramSender(), progress) for chat in [1, 2]
        ]), 3)
        service.download.assert_awaited_once()
        assert sorted(calls) == [1, 2]
        assert not any(p.exists() for p in artifacts)
    finally:
        await runtime.close()


def test_worker_video_missing_title_has_extractor_context(monkeypatch):
    from bot.services import ytdlp_worker
    item = {'id': '123', 'video': {'playAddr': 'https://cdn.example/a.mp4', 'width': 720, 'height': 1280}}
    monkeypatch.setattr(ytdlp_worker.TikTokIE, '_extract_web_data_and_status', lambda *a: (item, 0))
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        info = ytdlp_worker.extract_job(URL.replace('/photo/', '/video/'), ydl)
    assert info['id'] == '123'
    assert info['extractor'] == 'TikTok'
    assert info['formats'][0]['has_watermark'] is False


def test_cli_photo_output_does_not_expose_signed_source_urls(tmp_path, monkeypatch, capsys):
    import sys
    from unittest.mock import AsyncMock
    from bot import cli
    from bot.models import VideoInfo, PhotoInfo, DownloadResult
    result = DownloadResult(file_path=tmp_path / 'originals.zip', file_size_bytes=3,
        video_info=VideoInfo(id='123', webpage_url=URL, media_type='photo',
                            photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/image?signature=PRIVATE'])]))
    monkeypatch.setattr(sys, 'argv', ['download', URL])
    monkeypatch.setattr(cli.TikTokDownloader, 'download', AsyncMock(return_value=result))
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert 'signature' not in output
    assert 'cdn.example' not in output
    assert 'originals.zip' in output


def test_photo_route_uses_video_metadata_page_without_changing_media_kind(monkeypatch):
    """TikTok's photo view need not embed webapp.video-detail; video view does."""
    import json
    from types import SimpleNamespace
    from bot.services import ytdlp_worker

    requested = []
    item = {'id': '123', 'desc': 'Photo post', 'author': {'uniqueId': 'author'},
            'imagePost': {'images': [{'imageURL': {'urlList': ['https://cdn.example/1.jpg']},
                                      'imageWidth': 1200, 'imageHeight': 1800}]}}

    def page(self, url, video_id, *args, **kwargs):
        requested.append(url)
        scope = {'seo.abtest': {'canonical': 'https://www.tiktok.com/@author/video/123'}}
        if '/video/' in url:
            scope['webapp.video-detail'] = {'statusCode': 0, 'itemInfo': {'itemStruct': item}}
        html = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        html += json.dumps({'__DEFAULT_SCOPE__': scope}) + '</script>'
        return html, SimpleNamespace(url=url, extensions={})

    monkeypatch.setattr(ytdlp_worker.TikTokIE, '_download_webpage_handle', page)
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        info = ytdlp_worker.extract_job(URL, ydl)
    assert requested == ['https://www.tiktok.com/@author/video/123']
    assert info['media_type'] == 'photo'
    assert info['webpage_url'] == URL
    assert len(info['photos']) == 1
