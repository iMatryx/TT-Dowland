"""Telegram long-polling entry point with an offline wiring check."""

import argparse
import asyncio
import logging

from pydantic import ValidationError

from bot.config import Settings
from bot.models.exceptions import ConfigurationError
from bot.utils.logging import SecretFilter


def configure_logging(level: str, token: str | None = None) -> None:
    """Configure application-wide logging."""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger().setLevel(level)
    for handler in logging.getLogger().handlers:
        existing = next((f for f in handler.filters if isinstance(f, SecretFilter)), None)
        if existing is None:
            handler.addFilter(SecretFilter(token))
        elif token:
            existing.token = token


async def run_polling(settings: Settings) -> None:
    """Connect only when explicitly run with a configured BotFather token."""
    from bot.telegram_config import create_bot, create_dispatcher
    from bot.services.runtime import BotRuntime

    bot = create_bot(settings)
    runtime = BotRuntime(settings)
    try:
        await runtime.start()
        dispatcher = create_dispatcher(settings, runtime=runtime)
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False, handle_signals=True,
            tasks_concurrency_limit=settings.max_pending_requests + settings.max_concurrent_downloads + 4,
        )
    finally:
        try:
            await runtime.close()
        finally:
            await bot.session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="TikTok Ultra Downloader — Telegram long polling")
    parser.add_argument("--check", action="store_true", help="Verifică handlerele offline, fără token sau rețea")
    args = parser.parse_args()
    configure_logging("INFO")
    try:
        settings = Settings()
        configure_logging(settings.log_level, settings.bot_token)
        if args.check:
            from bot.telegram_config import create_dispatcher

            create_dispatcher(settings)
            print("Interfața Telegram este configurată. Verificare offline; nicio conexiune inițiată.")
        else:
            asyncio.run(run_polling(settings))
    except ConfigurationError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1
    except ValidationError:
        logging.getLogger(__name__).error("Configurație invalidă. Verifică variabilele din .env.")
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.getLogger(__name__).error("Pornirea Telegram a eșuat (%s). Verifică tokenul și conexiunea.", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

