"""Fixed source policy, with migration of previously saved preferences."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)

    quality: Literal['high'] = 'high'
    watermark: Literal['without'] = 'without'

    @model_validator(mode='before')
    @classmethod
    def migrate_legacy(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get('quality') in {'high', 'low', 'maximum', 'prefer_clean'}:
            data['quality'] = 'high'
        if data.get('watermark') in {'without', 'prefer_clean', 'watermarked', 'any'}:
            data['watermark'] = 'without'
        for key in ('video_as_document', 'photo_mode', 'ask_each_time'):
            data.pop(key, None)
        return data
