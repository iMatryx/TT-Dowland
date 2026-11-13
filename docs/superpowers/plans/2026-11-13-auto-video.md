# Automatic playable video with file and Info controls

Confirmed behavior: a submitted video link downloads and sends a playable video
automatically. Under it, the first button row is File + Info. File sends the
original as a document only on demand. Audio/ZIP remain secondary actions.
Photos keep their current selection flow.

1. Add failing integration tests for automatic send, subsequent owner-bound file
   action and Info toggles that preserve controls. Add expiry/busy/foreign tests.
2. Add an automatic preview delivery mode. Compatible originals send directly;
   incompatible originals get a verified H.264/AAC MP4 preview. Original bytes are
   unchanged and still available for file delivery. Bound conversion time/size
   and clean partials on cancellation or failure. Cache converted preview
   metadata separately from originals.
3. Wire owner/preferences through delivery and cache-hit paths, bind delivered
   buttons to their exact media message. A secondary action creates one editable
   progress message; it cannot overwrite or delete the playable video.
4. Update instructions and run full offline tests and local FFmpeg checks.

Review focus: cache-hit buttons, preservation of File on Info toggles, document
bytes after preview conversion, busy/foreign/expired controls, conversion cleanup.
Preserve existing dirty checkout; no deployment or commits in this task.

## Verification

- Automatic video, File/Info controls, on-demand original, caption toggles,
  callback ownership/expiry/busy checks and cache-hit recipient binding verified.
- Real local FFmpeg test confirms incompatible video becomes H.264 MP4 and
  original bytes remain unchanged. Oversize and cancellation remove partials.
- Independent review found retry lost preview mode and owner; reproduced with
  a failed automatic upload and fixed. The retry integration test passes.
- Final offline suite: 264 passed, 5 optional live tests skipped. Offline
  dispatcher check and diff whitespace check pass. No live Telegram send or
  deployment performed.
