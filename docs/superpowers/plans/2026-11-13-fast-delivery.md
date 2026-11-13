# Faster quality-preserving delivery

Goal: implement the user's supplied requirements in the existing pipeline, offline.
Architecture: keep original bytes and existing budgets; use high/any defaults, file-bound verification receipts and strictly typed Telegram cache reuse.

- [x] Add regressions for quality ranking, new versus saved preferences, file identity invalidation, output verification and cache separation.
- [x] Change selection defaults without migrating saved choices. Keep explicit clean/low options.
- [x] Bind successful verification to device, inode, size and nanosecond modification/change timestamps plus verified metadata; reuse only matching receipts. Rebind after controlled atomic publication. Probe every newly prepared output.
- [x] Reuse an unmodified compatible original's Telegram video ID for preview delivery; retain separate processed preview keys and delivery kinds. Preserve shared original leases, TTLs and upstream refresh.
- [x] Run affected and offline suites, CLI checks and dependency validation. Measure local ffprobe work using existing performance events; leave concurrency unchanged.

Review: same-size edits, replacement/symlinks, modified metadata, saved legacy choices, processed preview/document confusion. No real bot, Telegram sends, deployment or network benchmarks.

Validation: initial offline baseline 360 passed / 5 live deselected. Final offline
suite 370 passed / 5 live deselected. Wiring check, compileall, pip check and
`git diff --check` passed. Focused cache tests repeated after expanding expiry,
bot isolation and low-quality coverage. Independent read-only review found no
important issues. Benchmark method and measurements: `docs/performance.md`.
Existing workspace changes were retained; no commits, bot startup, Telegram
sends, live tests or deployment performed. Existing TTLs, upstream invalidation,
shared leases and concurrency budgets retained.

## Follow-up: fixed highest-quality, watermark-free source policy

The user's subsequent instruction supersedes the earlier high/any default and
preservation of legacy quality choices. All new and stored settings now resolve
to high/without. Removed low-quality selection/encoding, source controls and CLI
quality switches. Removed watermark fallback; unknown and marked video/photo
sources are unavailable. Source claims remain explicitly nonvisual. Audio is
independent and fixed at the best reported source plus 192 kbps MP3 delivery.

Preflight now considers the highest eligible clean video and audio independently,
so an oversized marked source cannot hide an eligible clean source. Regression
was reproduced RED and fixed GREEN. Source-selection cache keys still describe
the chosen format, with distinct original/preview/delivery types. Existing
lease, identity, TTL, concurrency and security mechanisms are unchanged.

## Follow-up: remove preferences entirely

Removed preferences UI, /settings, callback model/handlers, user preference model,
SQLite preference APIs and preference arguments/state across source selection,
download, pending requests and Telegram delivery. SQLite drops the obsolete
user_preferences table on opening; original media and Info tables remain.
The Info store is now named media_details and remains available with media cache
disabled. Removed legacy clean/maximum delivery controls. Source selection is
unconditionally highest-quality among sources explicitly identified without
watermark, with no fallback to unknown/watermarked variants.

Validation: 370 offline tests passed, 5 live deselected; obsolete preference tests
removed and replaced by menu/command/schema-removal and metadata-persistence
regressions. Offline wiring, compileall, pip check and diff whitespace checks
passed. Read-only independent review found no important regression. No real bot,
Telegram messages or deployment performed.
