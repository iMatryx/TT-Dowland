#!/bin/bash
# Run on the Docker host, after installing the current compose.deploy.yml/image.
set -euo pipefail
umask 077

if [[ $# -gt 1 ]]; then
    echo 'Usage: sudo bash deploy/enable-instagram.sh [config-directory]' >&2
    exit 2
fi
if [[ "$(id -u)" != 0 ]]; then
    echo 'Run this script with sudo to set ownership for container UID 10001.' >&2
    exit 1
fi
command -v python3 >/dev/null
command -v flock >/dev/null
config_dir="${1:-/opt/tt-dowland}"
config_dir="$(cd "$config_dir" && pwd -P)"
cookie="$config_dir/secrets/instagram.txt"
env_file="$config_dir/.env"
if [[ ! -f "$env_file" || ! -f "$config_dir/compose.deploy.yml" ]]; then
    echo 'Missing .env or compose.deploy.yml in the configuration directory.' >&2
    exit 1
fi
if [[ ! -s "$cookie" ]]; then
    echo 'Missing session: export Instagram cookies to secrets/instagram.txt first.' >&2
    exit 1
fi

# Share the updater's lock throughout validation, activation and rollback.
mkdir -p "$config_dir/.update-state"
exec 9>"$config_dir/.update-state/lock"
if ! flock -n 9; then
    echo 'Another update or session activation is running; retry after it finishes.' >&2
    exit 1
fi

compose=(docker compose --project-name tt-dowland --project-directory "$config_dir"
    --file "$config_dir/compose.deploy.yml" --env-file "$env_file")
"${compose[@]}" config --quiet
chown 10001:10001 "$config_dir/secrets" "$cookie"
chmod 700 "$config_dir/secrets"
chmod 400 "$cookie"

# Exercise the installed loader without contacting Instagram or Telegram.
# An old Compose file without the directory mount fails safely at this step.
"${compose[@]}" run --rm --no-deps -T --pull never \
    -e BOT_TOKEN= -e INSTAGRAM_COOKIES_FILE=/run/secrets/instagram.txt bot python -c '
import sys
from bot.services.ytdlp_worker import YoutubeDL, base_options, load_instagram_cookies
try:
    with YoutubeDL(base_options()) as ydl:
        load_instagram_cookies(ydl, "https://www.instagram.com/reel/sessioncheck/",
                              "/run/secrets/instagram.txt")
except Exception:
    print("Session validation failed: check cookie format, expiry, permissions and the Compose mount.", file=sys.stderr)
    sys.exit(1)
print("Cookie format and local expiry validated; online access remains to be checked.")
'

backup="$(mktemp "$config_dir/.env.instagram-backup.XXXXXX")"
cp -p "$env_file" "$backup"
chmod 600 "$backup"
python3 - "$env_file" <<'PY'
import os
from pathlib import Path
import re
import sys
import tempfile

target = Path(sys.argv[1])
original = target.stat()
text = target.read_text(encoding='utf-8')
setting = re.compile(r'^\s*(?:export\s+)?INSTAGRAM_COOKIES_FILE\s*=', re.I)
lines = [line for line in text.splitlines(keepends=True) if not setting.match(line)]
updated = ''.join(lines)
if updated and not updated.endswith('\n'):
    updated += '\n'
updated += 'INSTAGRAM_COOKIES_FILE=/run/secrets/instagram.txt\n'
with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                 prefix='.env.instagram-tmp.', delete=False) as out:
    temporary = Path(out.name)
    os.fchown(out.fileno(), original.st_uid, original.st_gid)
    os.fchmod(out.fileno(), 0o600)
    out.write(updated)
try:
    os.replace(temporary, target)
finally:
    temporary.unlink(missing_ok=True)
PY

if ! "${compose[@]}" up -d --no-deps --force-recreate --pull never bot; then
    cp -p "$backup" "$env_file"
    echo 'Activation failed; restored .env and attempting to restart the prior configuration.' >&2
    "${compose[@]}" up -d --no-deps --force-recreate --pull never bot || \
        echo 'Prior configuration could not be restarted; inspect the bot container.' >&2
    exit 1
fi
echo "Instagram session configured. Private environment backup: $backup"
echo 'Test an actual Reel through the bot or the downloader CLI to verify online access.'
