"""Compact and expanded captions, with measured values labeled honestly."""

from html import escape

from bot.utils.i18n import tr, localized_captions

from bot.models import VideoInfo, VideoFormat, PhotoFile, PhotoInfo, DownloadResult


def media_title(info: VideoInfo, *, limit: int = 160) -> str:
    return f"<b>{escape((info.title or (tr('Fotografii') if info.media_type == 'photo' else tr('Videoclip')))[:limit])}</b>"


@localized_captions
def selection_captions(info: VideoInfo) -> tuple[str, str]:
    """Keep the choice message compact; expose source metadata only via Info."""
    from bot.models.exceptions import SourceVariantUnavailableError
    from bot.services.quality import select_formats, select_photos
    from bot.utils.telegram_text import video_details

    summary = media_title(info)
    selection = None
    warning = ''
    if info.media_type == 'photo':
        info = select_photos(info)
    else:
        try:
            selection = select_formats(info.formats)
        except SourceVariantUnavailableError:
            warning = tr('ℹ️ Sursa nu oferă o variantă video identificată fără watermark. Poți alege doar audio.')
    source = selection.video if selection else None
    rows = [summary, video_details(info, None, source).partition('</b>\n')[2]]
    if info.duration_seconds is not None:
        rows.append(tr('⏱ Durată: {value0:g} s (sursă)', value0=info.duration_seconds))
    if source:
        audio = selection.audio or source
        rows.extend([tr('📄 Format: {value0}', value0=_text(source.ext, 12)),
                     tr('🎥 Codec video: {value0}', value0=_text(source.video_codec or tr('necunoscut'), 24)),
                     tr('🎵 Codec audio: {value0}', value0=_text(audio.audio_codec or tr('necunoscut'), 24)),
                     tr('📶 Bitrate: {value0:g} kb/s', value0=source.tbr) if source.tbr is not None else tr('📶 Bitrate: necunoscut')])
        if selection.estimated_size is not None:
            rows.append(tr('📦 Total fluxuri: {value0:.2f} MB (raportat de sursă)', value0=selection.estimated_size / 1000000))
    if warning:
        rows.append(warning)
    rows.extend([tr('💎 Calitate: maximă disponibilă la sursă'),
                 tr('💧 Watermark: numai variante identificate fără watermark; neverificat vizual.'),
                 tr('Valorile sursei se verifică după descărcare.')])
    if info.media_type == 'video':
        rows.append(tr('🎵 Doar audio: MP3, 192 kbps; watermark-ul nu se aplică sunetului.'))
    return summary, '\n'.join(rows)


def _text(value, limit=40):
    return escape(str(value)[:limit])


def _caption(lines):
    result = []
    for line in lines:
        if len('\n'.join([*result, line])) <= 1000:
            result.append(line)
    return '\n'.join(result)


def _watermark(source):
    if source is None or source.has_watermark is None:
        return tr('💧 Watermark: necunoscut; neverificat vizual')
    if source.has_watermark:
        return tr('💧 Watermark: prezent conform sursei; neverificat vizual')
    return tr('💧 Watermark: fără marcaj conform sursei; neverificat vizual')


@localized_captions
def video_captions(info: VideoInfo, verified: VideoFormat | None, source: VideoFormat | None,
                   size: int, *, document=False) -> tuple[str, str]:
    summary = media_title(info)
    fmt = verified or source
    resolution = f'{fmt.width} × {fmt.height}' if fmt and fmt.width and fmt.height else tr('necunoscută')
    measured = tr('verificată') if verified else tr('raportată de sursă')
    duration = verified.duration_seconds if verified and verified.duration_seconds is not None else info.duration_seconds
    rows = [media_title(info, limit=45), tr('📦 Dimensiune fișier: {value0:.2f} MB ({value1} bytes)', value0=size / 1000000, value1=size),
            tr('📹 Rezoluție {value0}: {value1}', value0=measured, value1=resolution),
            (tr('🎬 Variantă MP4 pentru redare în Telegram, convertită în H.264/AAC. Butonul „Ca fișier” trimite originalul.')
             if verified and verified.transcoded else
             tr('🎬 MP4 remuxat fără recodare; fluxurile originale sunt păstrate. Butonul „Ca fișier” trimite originalul.')
             if verified and verified.remuxed else tr('💎 Calitate: originală disponibilă, fără recodare/upscaling')),
            _watermark(source)]
    if duration is not None:
        rows.append(tr('⏱ Durată: {value0:g} s', value0=duration) + (tr(' (verificată)') if verified and verified.duration_seconds is not None else tr(' (sursă)')))
    if fmt:
        rows += [tr('📄 Format: {value0}', value0=_text(fmt.ext, 12)),
                 tr('🎞 FPS: {value0:g}', value0=fmt.fps) if fmt.fps is not None else tr('🎞 FPS: necunoscut'),
                 tr('📶 Bitrate total: {value0:g} kb/s', value0=fmt.tbr) if fmt.tbr is not None else tr('📶 Bitrate: necunoscut'),
                 tr('🎥 Codec video: {value0}', value0=_text(fmt.video_codec or tr('necunoscut'), 12)),
                 tr('🔊 Audio: {value0}', value0=tr('da') if fmt.has_audio else tr('nu') if fmt.has_audio is False else tr('necunoscut')),
                 tr('🎵 Codec audio: {value0}', value0=_text(fmt.audio_codec or tr('necunoscut'), 12))]
    if document:
        rows.append(tr('📁 Trimis ca fișier original'))
    rows.append(tr('👤 Autor: {value0}', value0=_text(info.uploader or tr('necunoscut'), 24)))
    return summary, _caption(rows)


@localized_captions
def photo_captions(info: VideoInfo, photo: PhotoFile, index: int, *, preview: bool) -> tuple[str, str]:
    summary = f"🖼 <b>{_text(info.title or tr('Fotografii TikTok'), 45)}</b> · {index + 1}/{len(info.photos)}"
    source = info.photos[index] if index < len(info.photos) else None
    rows = [summary, tr('📹 Rezoluție originală verificată: {value0} × {value1}', value0=photo.width, value1=photo.height),
            tr('📦 Dimensiune original: {value0:.2f} MB ({value1} bytes)', value0=photo.size / 1000000, value1=photo.size),
            tr('📄 Format original: {value0}', value0=_text(photo.file_path.suffix.lstrip('.'), 12)),
            tr('🖼 Preview Telegram: poate fi comprimat; valorile de mai sus descriu originalul.') if preview else tr('💎 Fișier original, fără recodare.'),
            _watermark(source), tr('👤 Autor: {value0}', value0=_text(info.uploader or tr('necunoscut'), 24))]
    return summary, _caption(rows)


@localized_captions
def zip_captions(result: DownloadResult) -> tuple[str, str]:
    summary = tr('🗜 <b>{value0}</b> · ZIP original', value0=_text(result.video_info.title or tr('Fotografii TikTok'), 45))
    dimensions = list(dict.fromkeys(f'{p.width}×{p.height}' for p in result.photos))
    return summary, _caption([summary, tr('🖼 Fotografii: {value0} (în ordinea postării)', value0=len(result.photos)),
        tr('📦 Dimensiune ZIP: {value0:.2f} MB ({value1} bytes)', value0=result.file_size_bytes / 1000000, value1=result.file_size_bytes),
        tr('📦 Total originale: {value0} bytes', value0=sum((p.size for p in result.photos))),
        tr('📹 Rezoluții originale verificate: ') + ', '.join(dimensions[:12]) + (' …' if len(dimensions) > 12 else ''),
        tr('💎 Originalele sunt păstrate fără recodare în ZIP.'),
        tr('💧 Watermark: neverificat vizual'), tr('👤 Autor: {value0}', value0=_text(result.video_info.uploader or tr('necunoscut'), 24))])


@localized_captions
def video_zip_captions(info, verified, source, size):
    short, expanded = video_captions(info, verified, source, size, document=True)
    summary = short
    lines = expanded.splitlines()[1:]
    lines = [line for line in lines if not line.startswith('📦')]
    return summary, _caption([summary, tr('📦 Dimensiune ZIP: {value0:.2f} MB ({value1} bytes)', value0=size / 1000000, value1=size),
                              tr('🗜 ZIP fără recodare; formatul și rezoluția descriu video-ul din interior.'), *lines])


@localized_captions
def audio_captions(info: VideoInfo, verified: VideoFormat | None, size: int) -> tuple[str, str]:
    summary = media_title(info)
    rows = [media_title(info, limit=45), tr('🎵 Sunet extras din videoclip · MP3'),
            tr('📦 Dimensiune fișier: {value0:.2f} MB ({value1} bytes)', value0=size / 1000000, value1=size),
            tr('ℹ️ Audio convertit în MP3; conversia nu îmbunătățește sursa.')]
    if verified:
        if verified.duration_seconds is not None:
            rows.append(tr('⏱ Durată verificată: {value0:g} s', value0=verified.duration_seconds))
        if verified.tbr is not None:
            rows.append(tr('📶 Bitrate total verificat: {value0:g} kb/s', value0=verified.tbr))
        rows.append(tr('🎵 Codec audio: {value0}', value0=_text(verified.audio_codec or tr('necunoscut'), 16)))
    rows.append(tr('👤 Autor: {value0}', value0=_text(info.uploader or tr('necunoscut'), 40)))
    return summary, _caption(rows)
