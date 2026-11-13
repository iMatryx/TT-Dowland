# Telegram menu and preferences plan

Approved scope: persistent four-button Telegram reply menu, independent video quality/original-document and photo delivery preferences saved in SQLite, ask-every-time default, cancellation of pending choices. Keep existing owner checks and download limits.

- [x] Tests: persistence across reopening, concurrent independent setting changes, user isolation; menu routing before links; preference ownership/validation; selected defaults and unavailable fallback; cancel ownership and busy protection.
- [x] Add validated UserPreferences model and SQLite preference methods/table without invalidating existing video cache; keep preferences active when media cache disabled and close database at runtime shutdown.
- [x] Add menu/preferences handlers and keyboards; wire dispatcher dependencies and /start. Keep /help; add limits information and /menu /settings /cancel aliases.
- [x] Share existing delivery operation between callbacks and automatic choices. Honor clean quality with original document delivery; filter unavailable/oversized actions before automatic delivery. Add owner-bound cancel callback for pending choices.
- [x] Run suite, offline dispatcher validation and whitespace check; document menu behavior and user-ID storage; independent review.

Defaults: quality=maximum, video_as_document=false, photo_mode=album, ask_each_time=true. Choosing prefer-clean falls back to maximum only if no clean source is identified; delivery mode remains independent. Cancel affects pending selection only, never claims to retract uploads. Existing files and changes remain in the user's current workspace.

Verification: 188 tests passed, 3 live tests skipped; offline dispatcher check, Compose configuration and git diff whitespace checks passed. Independent menu review reported no important findings. Telegram menu display/delivery has not been tested against the live API.
Implementation notes: preserve current uncommitted media work. Preferences share the existing SQLite connection and add an independent table, including when media cache is disabled. Cancel only removes idle choices owned by the requesting user and chat; does not cancel active uploads.
