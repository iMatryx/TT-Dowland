# Administrator-only restart

User authorizes implementing /restart restricted to configured Telegram numeric
IDs and securing configuration from GitHub exposure; no real bot/start/restart
or deployment during development.

- [x] Add hidden validated ADMIN_USER_IDS allowlist (empty denies all) and
  RESTART_ENABLED=false for unsupervised runs. Blank public example only.
- [x] Build bounded per-admin, private-chat/message-bound, 60-second single-use
  confirmation controller; no shell, exec, HTTP admin port, or credential prompt.
- [x] Add translated /restart and confirmation/cancel handlers. Authenticate the
  actual Telegram actor before every command and callback; never use username,
  forwarded user, message text, group-admin role or callback-provided identity.
- [x] Hide restart from public slash lists, show only scoped private admin menu.
  Valid confirmations may bypass cooldown but not authorization or shutdown gate.
- [x] Signal main lifecycle to stop polling, close runtime with its existing grace
  and cleanup, close Bot session, then return 75. Docker unless-stopped restarts
  container; document systemd Restart=on-failure and unsupervised stop behavior.
- [x] Test deny-all/unauthorized/forged/stale/replay/group cases and lifecycle
  with offline fake polling only. Run offline suite, configuration, dependency
  and compile checks; inspect Git secret exclusions without showing values.

No automatic first-user ownership and no administrator ID inferred from saved
user data. Local allowlist remains blank until user supplies their numeric ID.
Published code is intentionally reviewable; actual token/allowlist stay in ignored
.env. This secures bot restart, not unrelated SSH or server accounts.


Validation: restart controller/config/handler tests went RED before implementation
and GREEN afterward. Fake polling lifecycle confirms graceful close and exit75;
Telegram identity/confirmation tests cover unauthorized users, groups, malformed
and replayed callbacks, ack failure and cooldown exemption. Independent review
found no important security/correctness issues. Local .env is ignored/untracked
and chmod0600; actual token matched no tracked working files or staged blobs.
Allowlist remains blank pending the user's numeric ID, and local restart remains
disabled. No real bot, Telegram sends, actual process restart or deployment ran.
Final offline suite: 417 passed, 5 live deselected. Offline wiring,
compileall, pip check and git diff --check also passed.
