# Separate source preferences from delivery choices

User-approved scope: quality and watermark choices live exclusively in Preferences; every submitted link asks only chat/file/ZIP (plus cancel). Existing info/hide remains under delivered media.

Choices: high/low native source quality, independent watermark preference (prefer clean, explicitly watermarked, any). Prefer clean may fall back with truthful source status; requesting watermarked never adds a watermark and fails clearly if absent. No transcoding/downscaling. Preserve photo variant lists so quality/watermark are selected per image without reordering.

- [ ] Tests for independent selection, low native formats, photo variant choices, preference migration, per-request snapshot, only delivery buttons, ZIP video bytes and selection-aware cache/concurrency.
- [ ] Update validated preferences and legacy JSON migration. Remove automatic delivery/document/photo controls from UI; reject stale setting actions.
- [ ] Extend selection/downloader APIs with explicit quality and watermark, retaining legacy CLI parameters. Retain photo alternatives and select at analysis/download.
- [ ] Freeze preferences in pending requests; always ask delivery; propagate through callbacks/runtime, source validation and cache/share keys.
- [ ] Add original-video ZIP delivery with cancellation-safe cleanup and separate ZIP cache receipts. Preserve Info/Hide.
- [ ] Update tests/help/docs, run complete verification and independent review.
