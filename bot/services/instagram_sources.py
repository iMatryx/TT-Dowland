"""Map Instagram image posts to the existing ordered photo pipeline."""

from urllib.parse import urlsplit

from bot.models import PhotoInfo
from bot.models.exceptions import VideoUnavailableError, InstagramPhotoUnavailableError


def photo_metadata(product: dict, video_id: str | None = None) -> dict | None:
    images = product.get('carousel_media')
    if images is None:
        if product.get('media_type') != 1:
            return None
        images = [product]
    if not isinstance(images, list) or not images or len(images) > 1000:
        raise VideoUnavailableError('Missing or oversized Instagram photo album.')
    if any(not isinstance(image, dict) for image in images):
        raise VideoUnavailableError('Incomplete Instagram photo album.')
    if any(image.get('media_type') != 1 for image in images):
        return None
    photos = []
    for image in images:
        variants = []
        placeholder = False
        versions = image.get('image_versions2') or {}
        for candidate in versions.get('candidates') or []:
            if not isinstance(candidate, dict):
                continue
            url = candidate.get('url')
            if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
                continue
            parts = urlsplit(url)
            if parts.hostname == 'static.cdninstagram.com' and parts.path == '/rsrc.php/null.jpg':
                placeholder = True
                continue
            variants.append(PhotoInfo(
                urls=[url], width=candidate.get('width'), height=candidate.get('height'),
                watermark_evidence='Instagram image_versions2.candidates',
            ))
        if not variants:
            if placeholder:
                raise InstagramPhotoUnavailableError('Instagram photo placeholder: no actual image address was provided.')
            raise VideoUnavailableError('One Instagram photo has no supported image address.')
        selected = max(variants, key=lambda photo: (photo.width or 0) * (photo.height or 0))
        photo = selected.model_dump(mode='json')
        photo['variants'] = [variant.model_dump(mode='json', exclude={'variants'}) for variant in variants]
        photos.append(photo)
    user = product.get('user') or {}
    caption = product.get('caption') or {}
    identifier = product.get('code') or video_id or product.get('pk')
    if not identifier:
        raise VideoUnavailableError('Instagram did not report a post ID.')
    return {
        'id': str(identifier), 'title': caption.get('text'),
        'uploader': user.get('username') or user.get('full_name'),
        'media_type': 'photo', 'photos': photos,
    }
