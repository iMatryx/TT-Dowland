"""Telegram client configuration and offline dispatcher composition."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.token import TokenValidationError
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from bot.config import Settings
from bot.handlers import callbacks, commands, links
from bot.models.exceptions import ConfigurationError
from bot.services.downloader import TikTokDownloader
from bot.services.telegram_download import PendingRequests, TelegramDownloadService
from bot.services.telegram_sender import TelegramSender, LocalUploadPolicy
from bot.handlers.limits import AdmissionMiddleware
from bot.utils.telegram_retry import TelegramRetryMiddleware
from bot.handlers import menu, media_info, language
from bot.handlers.localization import LanguageMiddleware, RequireLanguageMiddleware
from bot.services.cache import SQLiteCache
from bot.services.conversion import ConversionLimiter
from bot.services.restart import RestartController
from bot.handlers import admin


def create_bot(settings: Settings) -> Bot:
    token = (settings.bot_token or "").strip()
    if not token:
        raise ConfigurationError("BOT_TOKEN lipsește. Completează .env doar când dorești conectarea reală.")
    try:
        api = {"api": TelegramAPIServer.from_base(settings.telegram_api_base, is_local=True)} if settings.telegram_api_base else {}
        session = AiohttpSession(limit=settings.http_max_connections, timeout=settings.upload_timeout_seconds, **api)
        session.middleware(TelegramRetryMiddleware(timeout=settings.upload_timeout_seconds))
        session._retry_managed = True
        return Bot(token, session=session,
                   default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    except (ValueError, TokenValidationError) as exc:
        raise ConfigurationError("BOT_TOKEN nu are un format valid.") from exc


def create_dispatcher(settings: Settings, runtime=None) -> Dispatcher:
    engine_settings = settings.model_copy(update={"download_dir": runtime.work_dir}) if runtime is not None else settings
    conversions = runtime.conversions if runtime is not None else ConversionLimiter(settings.max_concurrent_conversions)
    engine = TikTokDownloader(engine_settings, conversions=conversions, client=runtime.client if runtime is not None else None,
                               registry=runtime.registry if runtime is not None else None)
    if runtime is not None:
        runtime.extraction_caches.add(engine.extractor)
    media_details = runtime.media_details if runtime is not None else SQLiteCache(settings.cache_db_path, ttl=settings.cache_ttl_seconds)
    pending = PendingRequests()
    dispatcher = Dispatcher(
        disable_fsm=True,
        settings=settings,
        media_details=media_details, language_store=media_details,
        restart_controller=runtime.restart if runtime is not None else RestartController(settings.administrator_ids),
        download_service=TelegramDownloadService(engine), runtime=runtime,
        pending=pending, sender=TelegramSender(
            details=media_details, pending=pending, conversions=conversions,
            registry=runtime.registry if runtime is not None else None,
            policy=LocalUploadPolicy(settings.telegram_local_upload_limit_mb * 1_000_000) if settings.telegram_api_base else None,
            timeout=settings.upload_timeout_seconds),
    )
    dispatcher.update.outer_middleware(LanguageMiddleware(media_details))
    if runtime is not None:
        dispatcher.update.outer_middleware(AdmissionMiddleware(runtime))
    dispatcher.update.outer_middleware(RequireLanguageMiddleware(media_details))
    dispatcher.include_router(admin.create_router())
    dispatcher.include_router(language.create_router())
    dispatcher.include_router(commands.create_router())
    dispatcher.include_router(menu.create_router())
    dispatcher.include_router(media_info.create_router())
    dispatcher.include_router(callbacks.create_router())
    dispatcher.include_router(links.create_router())
    dispatcher.startup.register(commands.register_commands)
    if runtime is None:
        async def close_media_details(**kwargs):
            await engine.close()
            await media_details.close()
        dispatcher.shutdown.register(close_media_details)
    return dispatcher
