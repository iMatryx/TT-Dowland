"""Compact and expanded captions, with measured values labeled honestly."""

from html import escape

from bot.models import VideoInfo, VideoFormat, PhotoFile, PhotoInfo, DownloadResult


def media_title(info: VideoInfo, *, limit: int = 160) -> str:
    return f'<b>{escape((info.title or ("Fotografii" if info.media_type == "photo" else "Videoclip"))[:limit])}</b>'


def selection_captions(info: VideoInfo, preferences=None) -> tuple[str, str]:
    """Keep the choice message compact; expose source metadata only via Info."""
    from bot.models.preferences import UserPreferences
    from bot.models.exceptions import SourceVariantUnavailableError
    from bot.services.quality import select_formats, select_photos
    from bot.utils.telegram_text import video_details

    options = preferences or UserPreferences()
    summary = media_title(info)
    selection = None
    warning = ''
    if info.media_type == 'photo':
        info = select_photos(info, quality=options.quality, watermark=options.watermark)
    else:
        try:
            selection = select_formats(info.formats, quality=options.quality, watermark=options.watermark)
        except SourceVariantUnavailableError:
            warning = 'ℹ️ Sursa nu oferă o variantă video identificată fără watermark. Poți alege doar audio.'
    source = selection.video if selection else None
    rows = [summary, video_details(info, None, source).partition('</b>\n')[2]]
    if info.duration_seconds is not None:
        rows.append(f'⏱ Durată: {info.duration_seconds:g} s (sursă)')
    if source:
        audio = selection.audio or source
        rows.extend([f'📄 Format: {_text(source.ext, 12)}',
                     f'🎥 Codec video: {_text(source.video_codec or "necunoscut", 24)}',
                     f'🎵 Codec audio: {_text(audio.audio_codec or "necunoscut", 24)}',
                     f'📶 Bitrate: {source.tbr:g} kb/s' if source.tbr is not None else '📶 Bitrate: necunoscut'])
        if selection.estimated_size is not None:
            rows.append(f'📦 Total fluxuri: {selection.estimated_size / 1_000_000:.2f} MB (raportat de sursă)')
    if warning:
        rows.append(warning)
    rows.extend(['💎 Calitate: maximă disponibilă la sursă',
                 '💧 Watermark: numai variante identificate fără watermark; neverificat vizual.',
                 'Valorile sursei se verifică după descărcare.'])
    if info.media_type == 'video':
        rows.append('🎵 Doar audio: MP3, 192 kbps; watermark-ul nu se aplică sunetului.')
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
        return '💧 Watermark: necunoscut; neverificat vizual'
    if source.has_watermark:
        return '💧 Watermark: prezent conform sursei; neverificat vizual'
    return '💧 Watermark: fără marcaj conform sursei; neverificat vizual'


def video_captions(info: VideoInfo, verified: VideoFormat | None, source: VideoFormat | None,
                   size: int, *, document=False) -> tuple[str, str]:
    summary = media_title(info)
    fmt = verified or source
    resolution = f'{fmt.width} × {fmt.height}' if fmt and fmt.width and fmt.height else 'necunoscută'
    measured = 'verificată' if verified else 'raportată de sursă'
    duration = verified.duration_seconds if verified and verified.duration_seconds is not None else info.duration_seconds
    rows = [media_title(info, limit=45), f'📦 Dimensiune fișier: {size / 1_000_000:.2f} MB ({size} bytes)',
            f'📹 Rezoluție {measured}: {resolution}',
            ('🎬 Variantă MP4 pentru redare în Telegram, convertită în H.264/AAC. Butonul „Ca fișier” trimite originalul.'
             if verified and verified.transcoded else
             '🎬 MP4 remuxat fără recodare; fluxurile originale sunt păstrate. Butonul „Ca fișier” trimite originalul.'
             if verified and verified.remuxed else '💎 Calitate: originală disponibilă, fără recodare/upscaling'),
            _watermark(source)]
    if duration is not None:
        rows.append(f'⏱ Durată: {duration:g} s' + (' (verificată)' if verified and verified.duration_seconds is not None else ' (sursă)'))
    if fmt:
        rows += [f'📄 Format: {_text(fmt.ext, 12)}',
                 f'🎞 FPS: {fmt.fps:g}' if fmt.fps is not None else '🎞 FPS: necunoscut',
                 f'📶 Bitrate total: {fmt.tbr:g} kb/s' if fmt.tbr is not None else '📶 Bitrate: necunoscut',
                 f'🎥 Codec video: {_text(fmt.video_codec or "necunoscut", 12)}',
                 f'🔊 Audio: {"da" if fmt.has_audio else "nu" if fmt.has_audio is False else "necunoscut"}',
                 f'🎵 Codec audio: {_text(fmt.audio_codec or "necunoscut", 12)}']
    if document:
        rows.append('📁 Trimis ca fișier original')
    rows.append(f'👤 Autor: {_text(info.uploader or "necunoscut", 24)}')
    return summary, _caption(rows)


def photo_captions(info: VideoInfo, photo: PhotoFile, index: int, *, preview: bool) -> tuple[str, str]:
    summary = f'🖼 <b>{_text(info.title or "Fotografii TikTok", 45)}</b> · {index + 1}/{len(info.photos)}'
    source = info.photos[index] if index < len(info.photos) else None
    rows = [summary, f'📹 Rezoluție originală verificată: {photo.width} × {photo.height}',
            f'📦 Dimensiune original: {photo.size / 1_000_000:.2f} MB ({photo.size} bytes)',
            f'📄 Format original: {_text(photo.file_path.suffix.lstrip("."), 12)}',
            '🖼 Preview Telegram: poate fi comprimat; valorile de mai sus descriu originalul.' if preview else '💎 Fișier original, fără recodare.',
            _watermark(source), f'👤 Autor: {_text(info.uploader or "necunoscut", 24)}']
    return summary, _caption(rows)


def zip_captions(result: DownloadResult) -> tuple[str, str]:
    summary = f'🗜 <b>{_text(result.video_info.title or "Fotografii TikTok", 45)}</b> · ZIP original'
    dimensions = list(dict.fromkeys(f'{p.width}×{p.height}' for p in result.photos))
    return summary, _caption([summary, f'🖼 Fotografii: {len(result.photos)} (în ordinea postării)',
        f'📦 Dimensiune ZIP: {result.file_size_bytes / 1_000_000:.2f} MB ({result.file_size_bytes} bytes)',
        f'📦 Total originale: {sum(p.size for p in result.photos)} bytes',
        '📹 Rezoluții originale verificate: ' + ', '.join(dimensions[:12]) + (' …' if len(dimensions) > 12 else ''),
        '💎 Originalele sunt păstrate fără recodare în ZIP.',
        '💧 Watermark: neverificat vizual', f'👤 Autor: {_text(result.video_info.uploader or "necunoscut", 24)}'])


def video_zip_captions(info, verified, source, size):
    short, expanded = video_captions(info, verified, source, size, document=True)
    summary = short
    lines = expanded.splitlines()[1:]
    lines = [line for line in lines if not line.startswith('📦')]
    return summary, _caption([summary, f'📦 Dimensiune ZIP: {size / 1_000_000:.2f} MB ({size} bytes)',
                              '🗜 ZIP fără recodare; formatul și rezoluția descriu video-ul din interior.', *lines])


def audio_captions(info: VideoInfo, verified: VideoFormat | None, size: int) -> tuple[str, str]:
    summary = media_title(info)
    rows = [media_title(info, limit=45), '🎵 Sunet extras din videoclip · MP3',
            f'📦 Dimensiune fișier: {size / 1_000_000:.2f} MB ({size} bytes)',
            'ℹ️ Audio convertit în MP3; conversia nu îmbunătățește sursa.']
    if verified:
        if verified.duration_seconds is not None:
            rows.append(f'⏱ Durată verificată: {verified.duration_seconds:g} s')
        if verified.tbr is not None:
            rows.append(f'📶 Bitrate total verificat: {verified.tbr:g} kb/s')
        rows.append(f'🎵 Codec audio: {_text(verified.audio_codec or "necunoscut", 16)}')
    rows.append(f'👤 Autor: {_text(info.uploader or "necunoscut", 40)}')
    return summary, _caption(rows)
