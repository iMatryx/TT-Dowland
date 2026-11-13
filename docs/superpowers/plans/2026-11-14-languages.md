# Per-user language selection

Goal: Romanian, Russian and English interfaces, selected on first contact and on
every /start. Save language across restarts; /menu keeps the selected language.
Implementation remains offline; no real bot, sends or deployment.

- [x] Test and add a dedicated user_languages SQLite table, validated language
  codes and expiring, single-use, user/chat/message-bound selector tokens.
- [x] Add update middleware with task-local locale context, before admission so
  rate-limit notices are translated. Require selection before ordinary actions.
- [x] /start always opens the neutral language selector; selection shows the
  localized welcome and keyboard. Recognize menu labels from all three languages
  so old keyboards continue working after a change.
- [x] Translate commands/help/limits, buttons, errors, progress and media Info.
  Keep source titles/authors/codecs unchanged. Store all language versions of Info
  under the same scoped cache entry, and select at reading time.
- [x] Verify persistence with CACHE_ENABLED=false, isolation under concurrency,
  repeated /start, stale/forged callbacks, TTLs and unchanged source/cache policy.
- [x] Run affected and full offline tests, wiring, compile, dependency and whitespace
  checks. Document behavior and user-facing restart requirement.

Decisions: explicit user selection overrides Telegram account language. /start
clears the prior language until selection, while /menu does not. Old selectors
expire in 10 minutes and are superseded by a new /start. Group selectors belong
to the initiating user. Interface strings are translated at construction from
explicit templates, never by replacing words in dynamic source metadata.


Verification: 393 offline tests passed, 5 live tests deselected. Offline wiring,
compileall, pip check and git diff --check passed. Independent review identified
the slash-menu account-language mismatch; per-chat/private and per-member/group
command scopes fixed it with a RED→GREEN regression and full green suite. No
runtime concurrency budgets, source policy, TTLs or verification protections
changed. No real bot/network messages or deployment performed.
