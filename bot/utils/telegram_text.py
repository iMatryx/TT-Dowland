"""Romanian UI text; external metadata is escaped and length-bounded."""

from html import escape

from bot.models import VideoFormat, VideoInfo
from bot.models.exceptions import (
    CorruptFileError, DownloadTimeoutError, FileSizeLimitError, InsufficientSpaceError,
    InvalidURLError, MissingFFmpegError, NetworkError, PrivateVideoError,
    RegionalRestrictionError, TelegramDeliveryError, VideoUnavailableError, DeletedVideoError,
    QueueFullError, RateLimitedError,
)

START_TEXT = (
    "🎬 <b>TikTok Ultra Downloader</b>\n\n"
    "Salut! Trimite-mi un link TikTok și îți voi descărca videoclipul la cea mai bună calitate disponibilă!\n\n"
    "✨ Fără watermark, dacă este disponibil\n"
    "💎 Calitate originală maximă\n⚡ Descărcare rapidă\n📁 Fișier original"
)


def video_details(info: VideoInfo, verified: VideoFormat | None,
                  source: VideoFormat | None, size: int | None = None) -> str:
    fmt = verified or source
    dimensions = f"{fmt.width} × {fmt.height}" if fmt and fmt.width and fmt.height else "Necunoscută"
    fps = f"{fmt.fps:g}" if fmt and fmt.fps else "Necunoscut"
    watermark = "Necunoscut"
    if source and source.has_watermark is True:
        watermark = "Da (raportat de sursă)"
    elif source and source.has_watermark is False:
        watermark = "Nu (declarat de sursă; neverificat vizual)"
    estimated = size is None and source is not None and source.filesize_estimated
    reported_size = size if size is not None else source.filesize if source else None
    size_text = f"{reported_size / 1_000_000:.2f} MB" if reported_size is not None else "Necunoscută"
    if estimated:
        size_text += " (estimată)"
    return (
        f"🎬 <b>{escape((info.title or 'Videoclip TikTok')[:160])}</b>\n"
        f"📹 Rezoluție: {dimensions}\n🎞 FPS: {fps}\n📦 Dimensiune: {size_text}\n"
        "💎 Calitate: originală disponibilă, fără upscaling\n"
        f"👤 Autor: {escape((info.uploader or 'Necunoscut')[:100])}\n"
        f"💧 Watermark: {watermark}"
    )


def error_text(exc: Exception) -> str:
    """Translate domain errors without exposing raw errors, URLs or tracebacks."""
    if isinstance(exc, RateLimitedError):
        return f"⏳ Ai atins limita temporară. Încearcă din nou în {exc.retry_after} secunde."
    messages = (
        (QueueFullError, "Coada este plină sau botul se oprește. Încearcă din nou mai târziu."),
        (InvalidURLError, "Trimite un singur link TikTok valid către un videoclip public."),
        (FileSizeLimitError, "Fișierul este prea mare pentru limita locală sau pentru uploadul botului Telegram. Nu reduc artificial calitatea."),
        (DeletedVideoError, "Videoclipul a fost șters sau nu mai poate fi găsit."),
        (PrivateVideoError, "Videoclipul este privat sau necesită autentificare. Sunt acceptate doar videoclipuri publice."),
        (RegionalRestrictionError, "Videoclipul nu este disponibil în regiunea serverului. Nu încerc ocolirea restricției."),
        (DownloadTimeoutError, "Operația a durat prea mult. Încearcă din nou mai târziu."),
        (NetworkError, "Conexiunea cu sursa a eșuat. Încearcă din nou mai târziu."),
        (CorruptFileError, "Fișierul nu a trecut verificarea de integritate. Nu îl voi trimite."),
        (InsufficientSpaceError, "Nu există suficient spațiu de lucru pe server. Încearcă mai târziu."),
        (MissingFFmpegError, "Procesarea video nu este disponibilă momentan. Administratorul trebuie să verifice FFmpeg și ffprobe."),
        (TelegramDeliveryError, "Nu am putut finaliza trimiterea prin Telegram. Poți reîncerca folosind butonul."),
        (VideoUnavailableError, "Videoclipul este indisponibil sau nu poate fi procesat momentan."),
    )
    for kind, message in messages:
        if isinstance(exc, kind):
            return "❌ " + message
    return "❌ Operația nu a putut fi finalizată. Încearcă din nou mai târziu."
