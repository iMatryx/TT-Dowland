# Expandable media information

Approved behavior: Info/Hide buttons beneath delivered video, original documents and photos. Edit captions in place; no new download or media resend. Show only source-reported or measured parameters, distinguishing original photos from Telegram previews.

- [x] Tests first: persisted message-bound details, show/hide caption edits, cached video receipts, image albums and singleton/ZIP delivery, unavailable details, caption bounds/HTML escaping and API failures.
- [x] Add bounded caption rendering and SQLite per-message details (no paths/CDN URLs), with expiry and capacity; share runtime DB lifecycle already used by preferences.
- [x] Add static show/hide callback buttons and a caption-edit handler; resolve by bot/chat/message binding. Explicit show/hide actions remain idempotent.
- [x] Wire sender for uploads and cached video, singleton images and ZIP. Albums cannot pass reply_markup to sendMediaGroup: attach inline controls to each returned image message via editMessageReplyMarkup after saving details. A metadata failure must never trigger duplicate media uploads.
- [x] Run full suite, offline check and code review; document live API limitations and stored chat/message identifiers.

Default view: compact title/index. Expanded view includes available resolution, original byte size, format, duration, FPS, bitrate, codecs/audio, source watermark status, author and photo position. Never promise visually verified watermark absence. Preserve working behavior for standalone senders without a metadata store.

Validation: 199 tests passed, 3 live tests skipped; offline dispatcher, whitespace and Compose checks passed. Independent read-only review reported no important findings. No live Telegram requests or deployment performed.
Details records share SQLite lifecycle with preferences, use CACHE_TTL_SECONDS and a 10,000-message cap, and contain only rendered captions plus bot/chat/message identifiers. Standalone TelegramSender instances without a details store retain their existing captions. New dispatcher-created senders enable expandable info for all supported media paths. Previously sent messages are not retrofitted.
