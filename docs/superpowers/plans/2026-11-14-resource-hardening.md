# Audit resource-hardening implementation record

User authorized fixing all four locally identified audit issues. Work completed
in the current workspace, preserving prior uncommitted changes and credentials.

- [x] Reproduce scoped Telegram cooldown isolation failures, implement bounded
  chat/method state and serialized same-chat retries; preserve deadlines and
  uncertain-network single-attempt behavior.
- [x] Reproduce missing total analysis-cache budget; add configurable64MiB budget
  for retained serialized strings/keys/overhead reserve, byte/count LRU eviction,
  over-budget bypass and TTL/invalidation/shutdown accounting.
- [x] Reproduce unbounded process output; stream both pipes with combined4MiB
  default cap and sanitized domain error; preserve process-group kill/reap/drain
  for overflow, timeout and repeated cancellation.
- [x] Reproduce orphan preview recovery gap; include .preview- in cleanup and
  protect active preview/ZIP directories through runtime FileRegistry leases.
- [x] Update README and resource/cache documentation, including scope and memory
  limitations. Preserve quality, watermark, SSRF, original retention, source TTLs,
  upload limits and download/conversion concurrency.

Verification: new regressions observed RED then GREEN; final integrated suite
440 passed, 5 live deselected. Offline wiring, compileall, pip check and diff
whitespace checks passed. Independent review found no important regression.
No real bot/Telegram/server load, actual restart or deployment performed.

Residual boundary: output/cache caps are not complete parser isolation or a
whole-process RSS cap. Existing Docker memory/process limits remain in place.
Telegram does not expose flood-limit scope in retry_after; rejected methods
still honor their own cooldown, while truly bot-wide rejection may affect
multiple independently backed-off scopes.
