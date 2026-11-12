import pytest

from bot.services.extraction import metadata_from_ytdlp
from bot.models.exceptions import classify_error, PrivateVideoError, RegionalRestrictionError
from bot.models.exceptions import DeletedVideoError, DownloadTimeoutError, NetworkError


def test_metadata_uses_only_reported_fields() -> None:
    info = metadata_from_ytdlp({
        "id": "123", "uploader": "author", "title": "Title", "duration": 12,
        "formats": [{"format_id": "play", "ext": "mp4", "url": "https://cdn.test/a",
                     "width": 1080, "height": 1920, "fps": 30, "vcodec": "h264",
                     "acodec": "aac", "tbr": 4500, "filesize_approx": 12345},
                    {"format_id": "download", "ext": "mp4", "format_note": "watermarked",
                     "url": "https://cdn.test/b"}],
    }, "https://www.tiktok.com/@author/video/123")
    assert info.uploader == "author"
    assert info.formats[0].tbr == 4500
    assert info.formats[0].filesize_estimated is True
    assert info.formats[0].has_watermark is None
    assert info.formats[1].has_watermark is True
    assert info.formats[1].fps is None
    assert info.formats[1].width is None


@pytest.mark.parametrize(("message", "expected"), [
    ("This video is private", PrivateVideoError),
    ("Video has been deleted", DeletedVideoError),
    ("not available in your country", RegionalRestrictionError),
    ("not available from your location due to geo restriction", RegionalRestrictionError),
    ("connection timed out", DownloadTimeoutError),
    ("HTTP Error 503: service unavailable", NetworkError),
    ("Unable to download webpage: nodename nor servname provided", NetworkError),
])
def test_classifies_known_errors(message: str, expected: type[Exception]) -> None:
    assert isinstance(classify_error(message), expected)
