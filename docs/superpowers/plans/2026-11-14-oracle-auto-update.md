# Oracle automatic update implementation plan

**Goal:** Supply a repeatable installation on an existing Oracle VM and publish bot updates after pushes to main without server SSH credentials in GitHub.

**Architecture:** GitHub tests offline and publishes a multiarchitecture GHCR image. A root-owned systemd timer pulls the main image and recreates only the isolated tt-dowland bot. Credentials stay on the host. Existing local ARM Compose remains available.

**Constraints:** No live bot, deployment, paid service, host-wide pruning, concurrency increase, or secret publication. Keep named volumes and runtime restrictions. First installation needs Linux with systemd, Docker Compose >=2.24 and available resources.

1. Add subprocess tests with a fake Docker CLI for unchanged images, pull/check failures, failed startup rollback and suppression of repeated bad images. Observe failure before implementing.
2. Add compose.deploy.yml, deploy/update.sh, and fixed-path systemd service/timer. Serialize updates with flock; preflight offline; verify process stability; preserve previous image for rollback. Never restart an explicitly stopped bot automatically.
3. Add main/PR offline test workflow; publish main only after tests, serialized publishing, amd64/arm64 images, GITHUB_TOKEN package permission confined to publishing.
4. Add Romanian step-by-step instructions covering SSH/OS prerequisites, package access, root-owned files, private .env, first start, timer, troubleshooting, rollback, persistence and limits. Link from README.
5. Run updater tests, full offline suite, bash syntax, Compose config --quiet, pip check and diff checks. Actual image builds and Oracle/Telegram integration remain external verification. Obtain a read-only final review.

## Verification record

- Updater tests: RED on missing updater, then 8 passed using fake Docker/sleep/flock (no daemon or network). Locking itself requires util-linux on the Linux host.
- Full offline suite: 448 passed, 5 live tests deselected. Offline handler check, compileall, pip check, bash syntax, Compose config --quiet and diff whitespace checks passed.
- Independent read-only review found a backup path permission error in the guide; corrected to a private writable home directory with explicit commands.
- No commit, push, image build, deployment or Telegram send performed. Local Docker daemon is absent; GHCR publication and real Oracle startup require the user's installation steps.
