# TikTok media implementation plan

Goal: extend the existing bot with ordered photo downloads and distinct clean/maximum video choices.
Architecture: retain isolated yt-dlp workers, guarded network requests, original-byte artifacts, bounded runtime and SQLite video file-id cache. Extend VideoInfo/DownloadResult compatibly; photo results contain ordered verified images and a stored ZIP. Reuse runtime leases for photos, without caching Telegram photo previews as originals.
Constraints: Python 3.12+, aiogram 3.x, ARM64 Docker, FFmpeg, SQLite, polling; no deployment or transcoding.

- [x] Add failing tests for photo URL/metadata/order, source evidence, maximum versus clean selection, Telegram actions, album splitting, original ZIP, size/network failures and local API configuration.
- [x] Extend models and worker: obtain public item data using the installed TikTok extractor; parse imagePost images atomically, prefer original image variants, retain URL mirrors; preserve video source provenance.
- [x] Download photos through guarded worker with streamed byte limits, probe actual dimensions, publish ordered files plus ZIP atomically; preserve existing video behavior and cleanup.
- [x] Add source-based dynamic keyboard, validate actions on callbacks, propagate clean/maximum choice through download/cache/share keys. Send photo or document groups of 2–10 with singleton handling; preflight all files.
- [x] Configure optional Local Bot API endpoint and conservative upload caps, retaining cloud defaults. Never transcode originals.
- [x] Run regression suite and optional live tests when explicitly configured; review Docker/ARM64 and update usage/limitations.

Review focus: incomplete albums must fail without publishing partial results; unknown watermark must never become a verified absence; signed URLs must not enter callbacks/logs; oversized previews must fall back to documents; cache keys must separate quality intent; canceled tasks must clean all photo artifacts.

Validation: 167 tests passed, 3 live tests skipped (explicit opt-in not configured).
Offline dispatcher check, pip dependency check, git diff whitespace check and Compose config passed.
Real image ffprobe/FFmpeg decode exercised locally. Docker build/runtime not executed: Docker daemon socket absent.
Independent review findings addressed: reject changed media kinds before upload; omit signed photo URLs from CLI JSON; filter known oversized actions individually.
Design decision: SQLite file-id caching remains for video/document. Photos and ZIP share local verified artifacts during concurrent deliveries; no persistent photo cache, avoiding preview recompression and keeping the existing cache schema compatible.
Live TikTok/Telegram and Local Bot API end-to-end behavior remain unverified. No deployment performed.
