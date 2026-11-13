"""Local downloader CLI, independent of Telegram."""

import argparse
import asyncio
import sys

from pydantic import ValidationError

from bot.config import Settings
from bot.main import configure_logging
from bot.models.exceptions import ApplicationError
from bot.services.downloader import TikTokDownloader


def positive_seconds(value: str) -> float:
    import math

    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return parsed


async def download_once(settings: Settings, url: str, *, timeout: float):
    engine = TikTokDownloader(settings, timeout=timeout)
    try:
        return await engine.download(url)
    finally:
        await engine.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Download one public TikTok original, without Telegram.")
    parser.add_argument("url", help="Public TikTok video, photo post or short link")
    parser.add_argument("--timeout", type=positive_seconds, default=240,
                        help="Total deadline in seconds, including verification (default: 240)")
    args = parser.parse_args()
    try:
        settings = Settings()
        configure_logging(settings.log_level)
        result = asyncio.run(download_once(settings, args.url, timeout=args.timeout))
    except ValidationError:
        print("Configuration is invalid; check environment settings.", file=sys.stderr)
        return 2
    except ApplicationError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Download cancelled; temporary files cleaned.", file=sys.stderr)
        return 130
    print(result.model_dump_json(indent=2, exclude={"video_info": {"photos": {"__all__": {"urls": True, "variants": {"__all__": {"urls"}}}}}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
