# Instagram session installation

> Execution: inline in the existing project, as requested by the user.

**Goal:** Enable the existing Instagram cookie loader in Docker without manual
Compose edits, keeping configuration across automated image updates.

**Design:** Mount `./secrets` read-only at `/run/secrets` in both Compose files.
Keep anonymous access when `INSTAGRAM_COOKIES_FILE` is empty. A server-side Bash
script activates an existing `secrets/instagram.txt`, validates it with the real
loader offline, backs up `.env`, changes only the cookie setting, and recreates
only the bot. Restore `.env` if activation fails. Do not generate credentials or
claim that offline validation proves access to Instagram.

**Alternatives:** An extra Compose override would require changing the updater;
embedding cookies in the image would leak credentials. Use the directory mount.

**Files:** `compose.deploy.yml`, `docker-compose.yml`,
`deploy/enable-instagram.sh`, `tests/test_instagram_installation.py`,
`.env.example`, `docs/instagram-session.md`.

- [x] Add behavioral tests for missing/invalid sessions, activation, unchanged
  unrelated settings, and rollback on a failed restart. Use fake Docker and
  ownership commands; real filesystem edits and real Bash execution.
- [x] Observe tests fail before implementing the activation script.
- [x] Add read-only mounts and the script. Keep backups private; never print
  credentials or start Telegram during validation.
- [x] Validate rendered Compose, shell syntax, targeted tests, and full suite.
- [x] Review the final diff and document exact installation commands.

**Constraints:** No changes to unrelated containers, updater behavior, firewall,
or credentials. Actual authentication requires a user-provided Netscape cookie
file. No remote deployment until the SSH execution request succeeds.

**Verification:** 495 passed, 5 live tests skipped; `bash -n` and `git diff --check`
passed. Both Compose mounts verified through rendered Docker Compose JSON.
Review found concurrent activation/updater operations; added their shared lock
and a regression test, observed failing then passing. Reviewer confirmed fixed.
Remote installation and actual Instagram access remain unverified.
