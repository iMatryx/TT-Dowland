"""Normalize public TikTok item fields without synthesizing CDN URLs or pixels."""

from typing import Any

from bot.models import PhotoInfo
from bot.models.exceptions import VideoUnavailableError


def address_urls(address: Any) -> list[str]:
    if isinstance(address, str):
        return [address] if address.startswith(('https://', 'http://')) else []
    if isinstance(address, dict):
        values = address.get('urlList') or address.get('UrlList') or address.get('url_list') or []
        if isinstance(values, list):
            return [u for u in values if isinstance(u, str) and u.startswith(('https://', 'http://'))]
    if isinstance(address, list):
        return [u for a in address for u in address_urls(a.get('src') if isinstance(a, dict) else a)]
    return []


def photo_metadata(item: dict, url: str) -> dict | None:
    post = item.get('imagePost', item.get('image_post_info'))
    if post is None:
        return None
    if not isinstance(post, dict):
        raise VideoUnavailableError('Invalid photo post metadata.')
    images = post.get('images')
    if not isinstance(images, list) or not images or len(images) > 1000:
        raise VideoUnavailableError('Missing or oversized photo album.')
    photos = []
    for image in images:
        if not isinstance(image, dict):
            raise VideoUnavailableError('Incomplete photo album.')
        candidates = []
        for field, priority, watermark in (
            ('originImage', 3, None), ('originalImage', 3, None),
            ('imageURL', 3, None),
            ('displayImage', 2, None), ('display_image', 2, None),
            ('ownerWatermarkImage', 0, True), ('userWatermarkImage', 0, True),
        ):
            source = image.get(field)
            urls = address_urls(source)
            if not urls:
                continue
            width = source.get('width', image.get('imageWidth') if field == 'imageURL' else None)
            height = source.get('height', image.get('imageHeight') if field == 'imageURL' else None)
            photo = PhotoInfo(urls=urls, width=width, height=height,
                              has_watermark=source.get('hasWatermark', watermark),
                              watermark_evidence=f'TikTok imagePost.{field}')
            area = (photo.width or 0) * (photo.height or 0)
            candidates.append(((photo.has_watermark is not True, area, priority), photo))
        if not candidates:
            raise VideoUnavailableError('One photo has no supported public image address.')
        selected = max(candidates, key=lambda c: c[0])[1].model_dump(mode='json')
        selected['variants'] = [c[1].model_dump(mode='json', exclude={'variants'}) for c in candidates]
        photos.append(selected)
    author = item.get('author') or {}
    return {'id': str(item.get('id') or item.get('aweme_id') or url.rsplit('/', 1)[-1]),
            'title': item.get('desc'), 'uploader': author.get('uniqueId') or author.get('unique_id'),
            'media_type': 'photo', 'photos': photos, 'webpage_url': url}


def annotate_video_sources(info: dict, item: dict) -> None:
    """Record source roles, never infer from generic format IDs or CDN names.

    Playback addresses exclude TikTok's download overlay; creator-baked marks
    cannot be ruled out. This is source evidence, not visual verification.
    """
    video = item.get('video') or {}
    playback = set(address_urls(video.get('playAddr')))
    for rate in video.get('bitrateInfo') or []:
        playback.update(address_urls(rate.get('PlayAddr')))
    download = set(address_urls(video.get('downloadAddr')))
    for fmt in info.get('formats') or []:
        if fmt.get('url') in playback:
            fmt.update(has_watermark=False, watermark_evidence='TikTok playback source; creator marks not checked')
        elif fmt.get('url') in download:
            fmt.update(has_watermark=video.get('hasWatermark') if isinstance(video.get('hasWatermark'), bool) else True,
                       watermark_evidence='TikTok download source')
