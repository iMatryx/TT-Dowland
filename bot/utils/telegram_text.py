"""Romanian UI text; external metadata is escaped and length-bounded."""

from html import escape

from bot.models import VideoFormat, VideoInfo
from bot.models.exceptions import (
    CorruptFileError, DownloadTimeoutError, FileSizeLimitError, InsufficientSpaceError,
    InvalidURLError, MissingFFmpegError, NetworkError, PrivateVideoError,
    RegionalRestrictionError, TelegramDeliveryError, VideoUnavailableError, DeletedVideoError,
    QueueFullError, RateLimitedError,
    SourceVariantUnavailableError,
)

START_TEXT = (
    "🎬 <b>TikTok Ultra Downloader</b>\n\n"
    "Salut! Trimite-mi un link public TikTok, YouTube Shorts sau Instagram Reels. "
    "Videoclipul apare automat în chat, cu butoanele 📁 Ca fișier și ℹ️ Info dedesubt. "
    "Accept și fotografii/slideshow TikTok, la cea mai bună calitate disponibilă!\n\n"
    "✨ Fără watermark, dacă este disponibil\n"
    "💎 Calitate originală maximă\n⚡ Descărcare rapidă\n📁 Fișier original\n🎵 Doar audio — MP3"
)


def video_details(info: VideoInfo, verified: VideoFormat | None,
                  source: VideoFormat | None, size: int | None = None) -> str:
    if info.media_type == "photo":
        dimensions = list(dict.fromkeys(f"{p.width} × {p.height}" for p in info.photos if p.width and p.height))
        return (f"🖼 <b>{escape((info.title or 'Fotografii TikTok')[:160])}</b>\n"
                f"{len(info.photos)} fotografii, în ordinea originală.\n"
                f"Rezoluții raportate de sursă: {', '.join(dimensions[:8]) or 'necunoscute'}.\n"
                "Dimensiunile se verifică după descărcare. Albumul foto poate fi comprimat de Telegram; "
                "fișierele și ZIP păstrează originalele.\n"
                "Watermark: variante originale preferate; absența nu este verificată vizual.")
    fmt = verified or source
    dimensions = f"{fmt.width} × {fmt.height}" if fmt and fmt.width and fmt.height else "Necunoscută"
    fps = f"{fmt.fps:g}" if fmt and fmt.fps else "Necunoscut"
    watermark = "Necunoscut"
    if source and source.has_watermark is True:
        watermark = "Da (raportat de sursă)"
    elif source and source.has_watermark is False:
        watermark = "Nu (declarat de sursă; neverificat vizual)"
        if source.watermark_evidence and "playback" in source.watermark_evidence:
            watermark = "Variantă playback fără overlay TikTok (inferență din sursă; neverificat vizual)"
    estimated = size is None and source is not None and source.filesize_estimated
    reported_size = size if size is not None else source.filesize if source else None
    size_text = f"{reported_size / 1_000_000:.2f} MB" if reported_size is not None else "Necunoscută"
    if estimated:
        size_text += " (estimată)"
    preparation = ("🎬 Preview recodat în H.264/AAC" if verified and verified.transcoded else
                   "🎬 MP4 remuxat fără recodare" if verified and verified.remuxed else
                   "💎 Calitate: originală disponibilă, fără upscaling")
    return (
        f"🎬 <b>{escape((info.title or 'Videoclip')[:160])}</b>\n"
        f"📹 Rezoluție: {dimensions} ({'verificată' if verified else 'raportată de sursă'})\n🎞 FPS: {fps}\n📦 Dimensiune: {size_text}\n"
        f"{preparation}\n"
        f"👤 Autor: {escape((info.uploader or 'Necunoscut')[:100])}\n"
        f"💧 Watermark: {watermark}"
    )


def error_text(exc: Exception) -> str:
    """Translate domain errors without exposing raw errors, URLs or tracebacks."""
    if isinstance(exc, RateLimitedError):
        return f"⏳ Ai atins limita temporară. Încearcă din nou în {exc.retry_after} secunde."
    messages = (
        (SourceVariantUnavailableError, "Sursa nu oferă o variantă identificată fără watermark. Nu trimit variante cu watermark sau cu statut necunoscut."),
        (QueueFullError, "Coada este plină sau botul se oprește. Încearcă din nou mai târziu."),
        (InvalidURLError, "Trimite un singur link TikTok valid (video sau fotografii), YouTube Shorts sau Instagram Reels public."),
        (FileSizeLimitError, "Fișierul este prea mare pentru limita locală sau pentru uploadul botului Telegram. Nu reduc artificial calitatea."),
        (DeletedVideoError, "Postarea a fost ștearsă sau nu mai poate fi găsită."),
        (PrivateVideoError, "Postarea este privată sau necesită autentificare. Sunt acceptate doar postări publice."),
        (RegionalRestrictionError, "Postarea nu este disponibilă în regiunea serverului. Nu încerc ocolirea restricției."),
        (DownloadTimeoutError, "Operația a durat prea mult. Încearcă din nou mai târziu."),
        (NetworkError, "Conexiunea cu sursa a eșuat. Încearcă din nou mai târziu."),
        (CorruptFileError, "Fișierul nu a trecut verificarea de integritate. Nu îl voi trimite."),
        (InsufficientSpaceError, "Nu există suficient spațiu de lucru pe server. Încearcă mai târziu."),
        (MissingFFmpegError, "Procesarea video nu este disponibilă momentan. Administratorul trebuie să verifice FFmpeg și ffprobe."),
        (TelegramDeliveryError, "Nu am putut finaliza trimiterea prin Telegram. Poți reîncerca folosind butonul."),
        (VideoUnavailableError, "Postarea sau pista audio este indisponibilă momentan pentru extracție. Această eroare nu confirmă că postarea a fost ștearsă."),
    )
    for kind, message in messages:
        if isinstance(exc, kind):
            return "❌ " + message
    return "❌ Operația nu a putut fi finalizată. Încearcă din nou mai târziu."
