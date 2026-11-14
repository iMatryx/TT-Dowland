#!/bin/bash
# Root-owned on the host; never source .env or accept code from a remote hook.
set -euo pipefail
umask 077
config_dir="${1:-/opt/tt-dowland}"
image='ghcr.io/imatryx/tt-dowland:main'
mkdir -p "$config_dir/.update-state"
exec 9>"$config_dir/.update-state/lock"
flock -n 9 || exit 0
compose=(docker compose --project-name tt-dowland --project-directory "$config_dir"
    --file "$config_dir/compose.deploy.yml" --env-file "$config_dir/.env")
failed="$config_dir/.update-state/failed-image"

# Do not turn an intentionally stopped bot back on or perform first installation.
container="$("${compose[@]}" ps --all --quiet bot)"
if [[ -z "$container" ]]; then exit 0; fi
status="$(docker inspect --format '{{.State.Status}} {{.RestartCount}}' "$container")"
if [[ "$status" != running\ * && "$status" != restarting\ * ]]; then exit 0; fi
old="$(docker inspect --format '{{.Image}}' "$container")"
if ! BOT_IMAGE="$image" "${compose[@]}" pull --quiet bot; then
    echo 'Image pull failed; existing bot retained.' >&2
    exit 1
fi
new="$(docker image inspect --format '{{.Id}}' "$image")"
if [[ "$old" == "$new" ]]; then exit 0; fi
if [[ -f "$failed" && "$(cat "$failed")" == "$new" ]]; then exit 0; fi

# This only wires the handlers. It does not start polling or contact Telegram.
if ! BOT_IMAGE="$new" "${compose[@]}" run --rm --no-deps --pull never -e BOT_TOKEN= bot python -m bot.main --check; then
    printf '%s\n' "$new" >"$failed"
    echo 'Offline check failed; existing bot retained.' >&2
    exit 1
fi
# Preserve a reference even after the mutable main tag moves. No host-wide prune.
docker image tag "$old" ghcr.io/imatryx/tt-dowland:previous-local
healthy=true
if ! BOT_IMAGE="$new" "${compose[@]}" up -d --no-deps --no-build --pull never bot; then
    healthy=false
else
    # Process stability only: no claim that Telegram/source connectivity is healthy.
    container="$("${compose[@]}" ps --all --quiet bot)"
    for attempt in {1..10}; do
        sleep 2
        status="$(docker inspect --format '{{.State.Status}} {{.RestartCount}}' "$container" 2>/dev/null || true)"
        if [[ "$status" != 'running 0' ]]; then healthy=false; break; fi
    done
fi
if [[ "$healthy" == true ]]; then
    rm -f "$failed"
    echo 'Bot updated; process remained running during the startup observation.'
else
    printf '%s\n' "$new" >"$failed"
    echo 'Startup failed; restoring the previous bot image.' >&2
    BOT_IMAGE="$old" "${compose[@]}" up -d --no-deps --no-build --pull never bot
    exit 1
fi
