"""Romanian UI text; external metadata is escaped and length-bounded."""

from html import escape

from bot.utils.i18n import tr, localized

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
    "Trimite un link public TikTok, YouTube Shorts sau Instagram Reels. "
    "Videoclipul apare automat în chat."
)


@localized
def video_details(info: VideoInfo, verified: VideoFormat | None,
                  source: VideoFormat | None, size: int | None = None) -> str:
    if info.media_type == "photo":
        dimensions = list(dict.fromkeys(f'{p.width} × {p.height}' for p in info.photos if p.width and p.height))
        return (tr('🖼 <b>{value0}</b>\n{value1} fotografii, în ordinea originală.\nRezoluții raportate de sursă: {value2}.\nDimensiunile se verifică după descărcare. Albumul foto poate fi comprimat de Telegram; fișierele și ZIP păstrează originalele.\nWatermark: variante originale preferate; absența nu este verificată vizual.', value0=escape((info.title or tr('Fotografii TikTok'))[:160]), value1=len(info.photos), value2=', '.join(dimensions[:8]) or tr('necunoscute')))
    fmt = verified or source
    dimensions = f'{fmt.width} × {fmt.height}' if fmt and fmt.width and fmt.height else tr('Necunoscută')
    fps = f'{fmt.fps:g}' if fmt and fmt.fps else tr('Necunoscut')
    watermark = tr('Necunoscut')
    if source and source.has_watermark is True:
        watermark = tr('Da (raportat de sursă)')
    elif source and source.has_watermark is False:
        watermark = tr('Nu (declarat de sursă; neverificat vizual)')
        if source.watermark_evidence and "playback" in source.watermark_evidence:
            watermark = tr('Variantă playback fără overlay TikTok (inferență din sursă; neverificat vizual)')
    estimated = size is None and source is not None and source.filesize_estimated
    reported_size = size if size is not None else source.filesize if source else None
    size_text = f'{reported_size / 1000000:.2f} MB' if reported_size is not None else tr('Necunoscută')
    if estimated:
        size_text += tr(' (estimată)')
    preparation = (tr('🎬 Preview recodat în H.264/AAC') if verified and verified.transcoded else
                   tr('🎬 MP4 remuxat fără recodare') if verified and verified.remuxed else
                   tr('💎 Calitate: originală disponibilă, fără upscaling'))
    return (
        tr('🎬 <b>{value0}</b>\n📹 Rezoluție: {value1} ({value2})\n🎞 FPS: {value3}\n📦 Dimensiune: {value4}\n{value5}\n👤 Autor: {value6}\n💧 Watermark: {value7}', value0=escape((info.title or tr('Videoclip'))[:160]), value1=dimensions, value2=tr('verificată') if verified else tr('raportată de sursă'), value3=fps, value4=size_text, value5=preparation, value6=escape((info.uploader or tr('Necunoscut'))[:100]), value7=watermark)
    )


def error_text(exc: Exception) -> str:
    """Translate domain errors without exposing raw errors, URLs or tracebacks."""
    if isinstance(exc, RateLimitedError):
        return tr('⏳ Ai atins limita temporară. Încearcă din nou în {value0} secunde.', value0=exc.retry_after)
    messages = (
        (SourceVariantUnavailableError, tr('Sursa nu oferă o variantă identificată fără watermark. Nu trimit variante cu watermark sau cu statut necunoscut.')),
        (QueueFullError, tr('Coada este plină sau botul se oprește. Încearcă din nou mai târziu.')),
        (InvalidURLError, tr('Trimite un singur link TikTok valid (video sau fotografii), YouTube Shorts sau Instagram Reels public.')),
        (FileSizeLimitError, tr('Fișierul este prea mare pentru limita locală sau pentru uploadul botului Telegram. Nu reduc artificial calitatea.')),
        (DeletedVideoError, tr('Postarea a fost ștearsă sau nu mai poate fi găsită.')),
        (PrivateVideoError, tr('Postarea este privată sau necesită autentificare. Sunt acceptate doar postări publice.')),
        (RegionalRestrictionError, tr('Postarea nu este disponibilă în regiunea serverului. Nu încerc ocolirea restricției.')),
        (DownloadTimeoutError, tr('Operația a durat prea mult. Încearcă din nou mai târziu.')),
        (NetworkError, tr('Conexiunea cu sursa a eșuat. Încearcă din nou mai târziu.')),
        (CorruptFileError, tr('Fișierul nu a trecut verificarea de integritate. Nu îl voi trimite.')),
        (InsufficientSpaceError, tr('Nu există suficient spațiu de lucru pe server. Încearcă mai târziu.')),
        (MissingFFmpegError, tr('Procesarea video nu este disponibilă momentan. Administratorul trebuie să verifice FFmpeg și ffprobe.')),
        (TelegramDeliveryError, tr('Nu am putut finaliza trimiterea prin Telegram. Poți reîncerca folosind butonul.')),
        (VideoUnavailableError, tr('Postarea sau pista audio este indisponibilă momentan pentru extracție. Această eroare nu confirmă că postarea a fost ștearsă.')),
    )
    for kind, message in messages:
        if isinstance(exc, kind):
            return "❌ " + message
    return tr('❌ Operația nu a putut fi finalizată. Încearcă din nou mai târziu.')
