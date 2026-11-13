"""Application exception types."""


class ApplicationError(Exception):
    """Base exception for expected application errors."""


class ConfigurationError(ApplicationError):
    """Raised when application configuration is invalid."""


class TelegramDeliveryError(ApplicationError):
    """A verified file could not be delivered through the Telegram transport."""


class InvalidCachedFileError(TelegramDeliveryError):
    """Telegram explicitly rejected a cached file identifier."""


class InvalidURLError(ApplicationError):
    """The input or redirect is not an allowed TikTok video URL."""


class UnsafeAddressError(InvalidURLError):
    """A network destination is not a public internet address."""


class QueueFullError(ApplicationError):
    """The bounded work queue is full or shutting down."""


class RateLimitedError(ApplicationError):
    def __init__(self, retry_after: float, *, notify: bool = True) -> None:
        self.retry_after = max(1, int(retry_after + 0.999))
        self.notify = notify
        super().__init__("Temporary per-user request limit.")


class VideoUnavailableError(ApplicationError):
    """The video cannot be extracted or has no usable video stream."""


class DeletedVideoError(VideoUnavailableError):
    """The upstream service explicitly reports a deleted video."""


class SourceVariantUnavailableError(VideoUnavailableError):
    """The requested source watermark/quality variant is not available."""


class PrivateVideoError(VideoUnavailableError):
    """Private or login-required content is not supported."""


class RegionalRestrictionError(VideoUnavailableError):
    """The service reports a regional restriction."""


class NetworkError(ApplicationError):
    """The remote service or network failed."""


class DownloadTimeoutError(NetworkError):
    """A bounded external operation exceeded its time limit."""


class CorruptFileError(ApplicationError):
    """The downloaded file is missing, malformed, or not decodable."""


class InsufficientSpaceError(ApplicationError):
    """There is insufficient disk space for a safe download."""


class MissingFFmpegError(ApplicationError):
    """FFmpeg or ffprobe is unavailable."""


class FileSizeLimitError(ApplicationError):
    """The selected original exceeds the configured local file limit."""


def classify_error(message: str) -> ApplicationError:
    """Map explicit upstream signals; ambiguous failures stay unavailable."""
    text = message.lower()
    if "unsafe network destination" in text:
        return UnsafeAddressError("Unsafe network destination blocked.")
    if "no space left" in text or "errno 28" in text:
        return InsufficientSpaceError("Insufficient disk space.")
    if "file size limit" in text or "max-filesize" in text:
        return FileSizeLimitError("Original exceeds MAX_FILE_SIZE_MB.")
    if "ffmpeg" in text and any(word in text for word in ("not found", "not installed")):
        return MissingFFmpegError("FFmpeg is not installed or is not on PATH.")
    if any(word in text for word in (
        "permission denied", "read-only file system", "input/output error", "too many open files",
        "no such file or directory", "[errno 2]", "[errno 5]", "[errno 13]", "[errno 24]", "[errno 30]",
    )):
        return ApplicationError("Local filesystem operation failed.")
    if any(word in text for word in ("timed out", "timeout")):
        return DownloadTimeoutError("The remote operation timed out.")
    if any(word in text for word in ("private", "login required", "log in", "sign in")):
        return PrivateVideoError("Private or login-required videos are not supported.")
    if any(word in text for word in ("not available in your country", "geo restrict", "geo-restrict", "region", "not available from your location")):
        return RegionalRestrictionError("The service reports a regional restriction.")
    if any(word in text for word in ("deleted", "removed", "http error 404", "status code 404")):
        return DeletedVideoError("The service reports that the video was removed or not found.")
    if "http error 403" in text or "status code 403" in text:
        return VideoUnavailableError("Access denied by source; no retry or bypass.")
    if any(word in text for word in (
        "connection", "network", "resolve", "http error 5", "http error 429",
        "urlopen error", "unable to download", "name or service not known", "nodename nor servname",
    )):
        return NetworkError("Network failure or service access denied; no bypass attempted.")
    return VideoUnavailableError("Video unavailable or unsupported by the installed extractor.")


class ProcessOutputLimitError(ApplicationError):
    """An external operation exceeded the combined captured output limit."""
