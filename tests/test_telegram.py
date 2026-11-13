"""Telegram interaction tests; no real token or Telegram requests."""

from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendDocument, SendVideo
from aiogram.types import Chat, Message, Update

from bot.config import Settings
from bot.handlers.commands import start_handler, help_handler
from bot.handlers.links import link_handler
from bot.handlers.callbacks import callback_handler
from bot.models import DownloadResult, VideoFormat, VideoInfo
from bot.models.exceptions import VideoUnavailableError, FileSizeLimitError
from bot.services.telegram_download import PendingRequests
from bot.services.telegram_sender import CloudUploadPolicy, TelegramSender


@pytest.fixture
def interaction():
    status = SimpleNamespace(message_id=20, chat=SimpleNamespace(id=10), message_thread_id=None)
    status.edit_text = AsyncMock(return_value=status)
    status.answer = AsyncMock(return_value=status)
    message = SimpleNamespace(
        text="https://www.tiktok.com/@author/video/123", entities=[],
        from_user=SimpleNamespace(id=1), chat=SimpleNamespace(id=10),
        message_id=19, answer=AsyncMock(return_value=status),
    )
    info = VideoInfo(id="123", title="Title <b>outside</b>", uploader="author & co",
                     webpage_url=message.text, formats=[VideoFormat(has_watermark=False,
                         format_id="play", ext="mp4", width=1080, height=1920,
                         video_codec="h264", audio_codec="aac", has_audio=True,
                     )])
    service = SimpleNamespace(analyze=AsyncMock(return_value=info), download=AsyncMock())
    return SimpleNamespace(message=message, status=status, service=service,
                           pending=PendingRequests(), info=info, sender=TelegramSender())


@pytest.mark.asyncio
async def test_start(interaction) -> None:
    await start_handler(interaction.message)
    text = interaction.message.answer.call_args.args[0]
    assert "🎬 <b>TikTok Ultra Downloader</b>" in text
    assert "Fără watermark, dacă este disponibil" in text


@pytest.mark.asyncio
async def test_help(interaction) -> None:
    await help_handler(interaction.message, Settings(), interaction.sender)
    text = interaction.message.answer.call_args.args[0]
    assert "Ca fișier" in text
    assert "50 MB" in text
    assert "Premium" in text
    assert "upscaling" in text


@pytest.mark.asyncio
async def test_audio_callback_requests_audio_and_cleans(interaction, tmp_path):
    result = result_for(interaction, tmp_path, ext='mp3')
    result.audio_only = True
    result.verified_format = VideoFormat(has_watermark=False, format_id='probe', ext='mp3', has_audio=True, has_video=False)
    interaction.service.download.return_value = result
    item = interaction.pending.add(1, 10, 20, interaction.info)
    query = SimpleNamespace(data=f'dl:audio:{item.key}', from_user=SimpleNamespace(id=1),
                            message=interaction.status, answer=AsyncMock())
    interaction.status.message_thread_id = None
    bot = SimpleNamespace(send_audio=AsyncMock())
    await callback_handler(query, bot, interaction.service, interaction.pending, interaction.sender)
    assert interaction.service.download.call_args.kwargs['audio_only'] is True
    bot.send_audio.assert_awaited_once()
    assert interaction.pending.get(item.key) is None
    assert not result.file_path.exists()


@pytest.mark.asyncio
async def test_link_still_offers_audio_when_watermark_preference_is_unavailable(interaction):
    from bot.models.preferences import UserPreferences
    interaction.service.analyze.return_value.formats[0].has_watermark = True
    preferences = SimpleNamespace(get_preferences=AsyncMock(return_value=UserPreferences(watermark='watermarked')))
    await link_handler(interaction.message, interaction.service, interaction.pending, interaction.sender,
                       preferences=preferences)
    keyboard = interaction.status.edit_text.call_args.kwargs.get('reply_markup')
    assert keyboard is not None
    assert [b.callback_data.split(':')[1] for row in keyboard.inline_keyboard for b in row
            if b.callback_data.startswith('dl:')] == ['audio']


@pytest.mark.asyncio
async def test_valid_link_analyzes_and_offers_buttons(interaction) -> None:
    await link_handler(interaction.message, interaction.service, interaction.pending, interaction.sender)
    interaction.service.analyze.assert_awaited_once()
    interaction.service.download.assert_not_awaited()
    kwargs = interaction.status.edit_text.call_args.kwargs
    buttons = kwargs["reply_markup"].inline_keyboard
    assert [row[0].callback_data.split(":")[1] for row in buttons if row[0].callback_data.startswith("dl:")] == ["chat", "file", "zip", "audio"]
    assert "În chat" in buttons[0][0].text
    text = interaction.status.edit_text.call_args.args[0]
    assert "&lt;b&gt;outside&lt;/b&gt;" in text
    assert text == '<b>Title &lt;b&gt;outside&lt;/b&gt;</b>'
    assert 'author' not in text and 'Rezoluție' not in text


@pytest.mark.asyncio
async def test_invalid_link_does_not_invoke_engine(interaction) -> None:
    interaction.message.text = "https://tiktok.com.evil.test/@author/video/123"
    await link_handler(interaction.message, interaction.service, interaction.pending, interaction.sender)
    interaction.service.analyze.assert_not_awaited()
    assert "link TikTok valid" in interaction.message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_unavailable_video_has_friendly_message(interaction) -> None:
    interaction.service.analyze.side_effect = VideoUnavailableError("internal extractor details")
    await link_handler(interaction.message, interaction.service, interaction.pending, interaction.sender)
    text = interaction.status.edit_text.call_args.args[0]
    assert "indisponibil" in text
    assert "internal extractor" not in text


def result_for(interaction, tmp_path: Path, *, ext: str = "mp4") -> DownloadResult:
    path = tmp_path / f"original.{ext}"
    path.write_bytes(b"original")
    return DownloadResult(
        video_info=interaction.info, file_path=path, file_size_bytes=8,
        format=interaction.info.formats[0],
        verified_format=VideoFormat(has_watermark=False, format_id="ffprobe", ext=ext, width=1080, height=1920,
                                    fps=30, video_codec="h264", audio_codec="aac", has_audio=True),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["video", "document"])
async def test_callback_downloads_and_sends_selected_mode(interaction, tmp_path: Path, mode: str) -> None:
    pending = interaction.pending.add(1, 10, 20, interaction.info)
    result = result_for(interaction, tmp_path)
    interaction.service.download.return_value = result
    bot = SimpleNamespace(send_video=AsyncMock(), send_document=AsyncMock())
    query = SimpleNamespace(data=f"dl:{mode}:{pending.key}", from_user=SimpleNamespace(id=1),
                            message=interaction.status, answer=AsyncMock())
    await callback_handler(query, bot, interaction.service, interaction.pending, interaction.sender)
    if mode == "video":
        bot.send_video.assert_awaited_once()
        bot.send_document.assert_not_awaited()
    else:
        bot.send_document.assert_awaited_once()
        bot.send_video.assert_not_awaited()
    assert not result.file_path.exists()
    assert interaction.status.edit_text.call_args.args[0].startswith("✅")
    assert interaction.pending.get(pending.key) is None


@pytest.mark.asyncio
async def test_foreign_user_cannot_use_button(interaction) -> None:
    pending = interaction.pending.add(1, 10, 20, interaction.info)
    query = SimpleNamespace(data=f"dl:video:{pending.key}", from_user=SimpleNamespace(id=2),
                            message=interaction.status, answer=AsyncMock())
    await callback_handler(query, SimpleNamespace(), interaction.service, interaction.pending, interaction.sender)
    interaction.service.download.assert_not_awaited()
    assert query.answer.call_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_file_too_large_is_not_uploaded(interaction, tmp_path: Path) -> None:
    result = result_for(interaction, tmp_path)
    with result.file_path.open("wb") as file:
        file.truncate(CloudUploadPolicy().max_upload_bytes + 1)
    bot = SimpleNamespace(send_video=AsyncMock(), send_document=AsyncMock())
    with pytest.raises(FileSizeLimitError):
        await interaction.sender.send(bot, 10, result, "document")
    bot.send_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_incompatible_video_falls_back_to_original_document(interaction, tmp_path: Path) -> None:
    result = result_for(interaction, tmp_path, ext="mkv")
    bot = SimpleNamespace(send_video=AsyncMock(), send_document=AsyncMock())
    assert await interaction.sender.send(bot, 10, result, "video") == "document"
    bot.send_video.assert_not_awaited()
    bot.send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_sending_failure_is_sanitized_and_file_cleaned(interaction, tmp_path: Path) -> None:
    pending = interaction.pending.add(1, 10, 20, interaction.info)
    result = result_for(interaction, tmp_path)
    interaction.service.download.return_value = result
    bot = SimpleNamespace(send_video=AsyncMock(), send_document=AsyncMock(side_effect=TelegramNetworkError(
        method=SendDocument(chat_id=10, document="file"), message="sensitive connection details",
    )))
    query = SimpleNamespace(data=f"dl:document:{pending.key}", from_user=SimpleNamespace(id=1),
                            message=interaction.status, answer=AsyncMock())
    await callback_handler(query, bot, interaction.service, interaction.pending, interaction.sender)
    assert not result.file_path.exists()
    assert not pending.busy
    text = interaction.status.edit_text.call_args.args[0]
    assert "trimiterea" in text
    assert "sensitive" not in text


def test_pending_requests_expire_and_are_bounded(interaction) -> None:
    now = [0.0]
    store = PendingRequests(ttl=10, capacity=1, clock=lambda: now[0])
    first = store.add(1, 10, 20, interaction.info)
    second = store.add(1, 10, 21, interaction.info)
    assert store.get(first.key) is None
    now[0] = 11
    assert store.get(second.key) is None


@pytest.mark.asyncio
async def test_busy_callback_does_not_start_second_download(interaction) -> None:
    item = interaction.pending.add(1, 10, 20, interaction.info)
    item.busy = True
    query = SimpleNamespace(data=f"dl:video:{item.key}", from_user=SimpleNamespace(id=1),
                            message=interaction.status, answer=AsyncMock())
    await callback_handler(query, SimpleNamespace(), interaction.service, interaction.pending, interaction.sender)
    interaction.service.download.assert_not_awaited()
    assert "deja în curs" in query.answer.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["dl:wrong:token", "dl:video:expired", "dl:video:key:extra"])
async def test_invalid_or_expired_callback_is_answered(interaction, data: str) -> None:
    query = SimpleNamespace(data=data, from_user=SimpleNamespace(id=1),
                            message=interaction.status, answer=AsyncMock())
    await callback_handler(query, SimpleNamespace(), interaction.service, interaction.pending, interaction.sender)
    interaction.service.download.assert_not_awaited()
    assert query.answer.call_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["video", "document"])
async def test_real_dispatcher_routes_link_and_callback_with_mock_session(
    interaction, tmp_path: Path, mode: str,
) -> None:
    from bot.telegram_config import create_dispatcher

    calls = []
    session = AsyncMock()
    bot = Bot("123456:TEST_ONLY_NOT_A_REAL_TOKEN", session=session)

    async def request(client, method, timeout=None):
        calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return Message(
            message_id=getattr(method, "message_id", None) or 20,
            date=datetime.now(timezone.utc), chat=Chat(id=10, type="private"),
            text=getattr(method, "text", None), reply_markup=getattr(method, "reply_markup", None),
        ).as_(bot)

    session.side_effect = request
    dispatcher = create_dispatcher(Settings(DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / "cache.sqlite3"))
    dependencies = dict(download_service=interaction.service, pending=interaction.pending,
                        sender=interaction.sender)
    result = result_for(interaction, tmp_path)
    interaction.service.download.return_value = result
    user = {"id": 1, "is_bot": False, "first_name": "Test"}
    message = {"message_id": 19, "date": 1, "chat": {"id": 10, "type": "private"},
               "from": user, "text": interaction.message.text}
    try:
        item = interaction.pending.add(1, 10, 20, interaction.info)
        data = f'dl:{mode}:{item.key}'
        await dispatcher.feed_update(bot, Update.model_validate({
            "update_id": 2, "callback_query": {
                "id": "callback-1", "from": user, "chat_instance": "chat", "data": data,
                "message": {**message, "message_id": 20},
            },
        }), **dependencies)
        assert any(isinstance(call, SendVideo if mode == "video" else SendDocument) for call in calls)
        assert not result.file_path.exists()
    finally:
        await dispatcher["preferences"].close()
        await bot.session.close()
