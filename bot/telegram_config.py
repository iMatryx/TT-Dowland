"""Telegram client configuration and offline dispatcher composition."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.token import TokenValidationError
from aiogram.client.session.aiohttp import AiohttpSession

from bot.config import Settings
from bot.handlers import callbacks, commands, links
from bot.models.exceptions import ConfigurationError
from bot.services.downloader import TikTokDownloader
from bot.services.telegram_download import PendingRequests, TelegramDownloadService
from bot.services.telegram_sender import TelegramSender
from bot.handlers.limits import AdmissionMiddleware
from bot.utils.telegram_retry import TelegramRetryMiddleware


def create_bot(settings: Settings) -> Bot:
    token = (settings.bot_token or "").strip()
    if not token:
        raise ConfigurationError("BOT_TOKEN lipsește. Completează .env doar când dorești conectarea reală.")
    try:
        session = AiohttpSession(limit=settings.http_max_connections, timeout=settings.upload_timeout_seconds)
        session.middleware(TelegramRetryMiddleware(timeout=settings.upload_timeout_seconds))
        session._retry_managed = True
        return Bot(token, session=session,
                   default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    except (ValueError, TokenValidationError) as exc:
        raise ConfigurationError("BOT_TOKEN nu are un format valid.") from exc


def create_dispatcher(settings: Settings, runtime=None) -> Dispatcher:
    engine_settings = settings.model_copy(update={"download_dir": runtime.work_dir}) if runtime is not None else settings
    engine = TikTokDownloader(engine_settings, client=runtime.client if runtime is not None else None,
                               registry=runtime.registry if runtime is not None else None)
    dispatcher = Dispatcher(
        disable_fsm=True,
        settings=settings,
        download_service=TelegramDownloadService(engine), runtime=runtime,
        pending=PendingRequests(), sender=TelegramSender(timeout=settings.upload_timeout_seconds),
    )
    if runtime is not None:
        dispatcher.update.outer_middleware(AdmissionMiddleware(runtime))
    dispatcher.include_router(commands.create_router())
    dispatcher.include_router(callbacks.create_router())
    dispatcher.include_router(links.create_router())
    return dispatcher
