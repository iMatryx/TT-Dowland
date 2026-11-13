"""Explicit template translation and task-local locale; never rewrite user data."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json

from bot.locales.core import CORE

LANGUAGES = ('ro', 'ru', 'en')
_language: ContextVar[str] = ContextVar('interface_language', default='ro')
_rendering: ContextVar[bool] = ContextVar('rendering_languages', default=False)
_PREFIX = '\x1ei18n:'


def current_language() -> str:
    return _language.get()


@contextmanager
def language_context(language: str):
    if language not in LANGUAGES:
        raise ValueError('Unsupported language')
    token = _language.set(language)
    try:
        yield
    finally:
        _language.reset(token)


def tr(source: str, **values) -> str:
    from bot.locales.media import MEDIA
    catalog = CORE.get(source) or MEDIA.get(source) or {}
    text = catalog.get(current_language(), source)
    return text.format(**values) if values else text


def translations(source: str) -> set[str]:
    result = set()
    for language in LANGUAGES:
        with language_context(language):
            result.add(tr(source))
    return result


class LocalizedText(str):
    def __new__(cls, variants: dict[str, str]):
        value = super().__new__(cls, variants[current_language()])
        value.translations = dict(variants)
        return value

    def __getnewargs__(self):
        return (self.translations,)


def _multilingual(function, pair):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _rendering.get():
            return function(*args, **kwargs)
        token = _rendering.set(True)
        try:
            rendered = {}
            for language in LANGUAGES:
                with language_context(language):
                    rendered[language] = function(*args, **kwargs)
            if pair:
                return tuple(LocalizedText({lang: result[i] for lang, result in rendered.items()}) for i in (0, 1))
            return LocalizedText(rendered)
        finally:
            _rendering.reset(token)
    return wrapped


def localized(function):
    return _multilingual(function, False)


def localized_captions(function):
    return _multilingual(function, True)


def serialize_text(text: str) -> str:
    if isinstance(text, LocalizedText):
        if set(text.translations) != set(LANGUAGES) or any(len(v) > 1024 for v in text.translations.values()):
            raise ValueError('Invalid multilingual captions')
        return _PREFIX + json.dumps(text.translations, ensure_ascii=False)
    return text


def deserialize_text(text: str) -> str:
    if not text.startswith(_PREFIX):
        return text  # Backward-compatible plain captions from before localization.
    variants = json.loads(text[len(_PREFIX):])
    if (not isinstance(variants, dict) or set(variants) != set(LANGUAGES)
            or any(not isinstance(v, str) or len(v) > 1024 for v in variants.values())):
        raise ValueError('Invalid multilingual captions')
    return LocalizedText(variants)
