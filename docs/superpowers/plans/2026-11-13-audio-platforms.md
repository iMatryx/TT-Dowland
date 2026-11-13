# Audio, YouTube Shorts and Instagram Reels

User request: add an audio-only button for videos and download support for YouTube
Shorts and Instagram Reels, retaining the existing TikTok/photo workflow.

Implementation choices: audio is MP3, generated with FFmpeg from a direct audio
stream when available or a video with audio otherwise. Probe and size-check the
result before sending. Exact platform hosts and individual clip paths only;
TikTok keeps its custom extractor, other platforms use their named yt-dlp
extractors. Public posts only, within current queue, timeout and upload limits.

1. Add URL and extractor-routing tests, observe failures, implement canonical
   URLs and explicit platform dispatch. Test redirects and hostile hosts.
2. Add tests for audio selection, conversion/probing, buttons and delivery;
   implement the audio download option and Telegram audio sends, including cache
   isolation between audio/video and between platforms.
3. Update menu/help/docs and YouTube runtime dependencies. Run the complete
   offline suite, real local FFmpeg conversion and dispatcher checks. Optional
   source live checks use explicitly supplied fixture URLs.

Review focus: unknown audio metadata; silent videos; rejected playlists/profile
URLs; FFmpeg cancellation/oversize cleanup; concurrent cache reuse across modes
and source hosts; failures requiring source authentication.

Work in the existing checkout because it contains substantial prior uncommitted
work. Preserve that work, do not commit or deploy as part of this request.

## Verification record

- URL normalization/dispatch and audio implementation completed.
- Independent review found the watermark preference could block audio; two
  reproducing tests failed before the fix and pass after it.
- Full suite: 242 passed, 5 optional live tests skipped. Real local FFmpeg
  conversion, probing, oversize rejection and cancellation cleanup covered.
- Offline dispatcher check and Python compilation passed; diff whitespace clean.
- Dependency installation was blocked by sandbox DNS. Retried with network
  approval requested; awaiting that approval. Real source downloads have not
  been verified, and no Telegram messages were sent.
