"""Run the activation script with real files and simulated Docker operations."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/enable-instagram.sh'


@pytest.fixture
def installation(tmp_path):
    config = tmp_path / 'bot config'
    config.mkdir()
    (config / 'compose.deploy.yml').write_text('services: {}\n')
    original = 'BOT_TOKEN=private-token\n# Keep this comment\nCACHE_ENABLED=true\n'
    (config / '.env').write_text(original)
    secret = config / 'secrets' / 'instagram.txt'
    secret.parent.mkdir()
    secret.write_text('private-cookie-content')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    (bindir / 'id').write_text('#!/bin/sh\necho 0\n')
    # CI is unprivileged; ownership changes require root only on the real host.
    (bindir / 'chown').write_text('#!/bin/sh\nexit 0\n')
    # macOS has no util-linux flock; use real POSIX locking on the inherited FD.
    (bindir / 'flock').write_text('''#!/usr/bin/env python3
import fcntl, sys
try:
    fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(1)
''')
    (bindir / 'docker').write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ['INSTALL_TEST_ROOT'])
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as out:
    out.write(json.dumps(args) + '\\n')
if 'run' in args:
    assert 'BOT_TOKEN=' in args
    assert 'INSTAGRAM_COOKIES_FILE=/run/secrets/instagram.txt' in args
    assert 'python' in args and '-c' in args
    sys.exit(int(os.environ.get('COOKIE_CHECK_FAILURE', '0')))
if 'up' in args:
    assert args[-1] == 'bot' and '--no-deps' in args
    marker = root / 'activation_attempted'
    if os.environ.get('RESTART_FAILURE') == '1' and not marker.exists():
        marker.touch()
        sys.exit(1)
''')
    for path in bindir.iterdir():
        path.chmod(0o755)

    def run(**env_changes):
        env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'],
                   INSTALL_TEST_ROOT=str(tmp_path), **env_changes)
        result = subprocess.run(['bash', str(SCRIPT), str(config)],
                                env=env, capture_output=True, text=True)
        calls_path = tmp_path / 'calls.jsonl'
        calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
        assert 'private-token' not in result.stdout + result.stderr
        assert 'private-cookie-content' not in result.stdout + result.stderr
        return result, calls

    return config, original, secret, run


def test_missing_cookie_does_not_change_configuration_or_restart(installation):
    config, original, secret, run = installation
    secret.unlink()
    result, calls = run()
    assert result.returncode != 0
    assert 'instagram.txt' in result.stderr
    assert (config / '.env').read_text() == original
    assert not any('up' in call for call in calls)


def test_invalid_cookie_does_not_change_configuration_or_restart(installation):
    config, original, _, run = installation
    result, calls = run(COOKIE_CHECK_FAILURE='1')
    assert result.returncode != 0
    assert any('run' in call for call in calls)
    assert (config / '.env').read_text() == original
    assert not any('up' in call for call in calls)


@pytest.mark.parametrize('previous', ['', 'export INSTAGRAM_COOKIES_FILE=/old/session\n'])
def test_activation_preserves_settings_and_private_backup(installation, previous):
    config, original, secret, run = installation
    (config / '.env').write_text(original + previous)
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert (config / '.env').read_text() == original + 'INSTAGRAM_COOKIES_FILE=/run/secrets/instagram.txt\n'
    assert secret.read_text() == 'private-cookie-content'
    backups = list(config.glob('.env.instagram-backup.*'))
    assert len(backups) == 1
    assert backups[0].read_text() == original + previous
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert (config / '.env').stat().st_mode & 0o777 == 0o600
    assert secret.stat().st_mode & 0o777 == 0o400
    restarts = [call for call in calls if 'up' in call]
    assert len(restarts) == 1
    assert '--force-recreate' in restarts[0]


def test_failed_activation_restores_previous_environment(installation):
    config, original, _, run = installation
    result, calls = run(RESTART_FAILURE='1')
    assert result.returncode != 0
    assert (config / '.env').read_text() == original
    assert len([call for call in calls if 'up' in call]) == 2


def test_active_updater_prevents_activation(installation):
    config, original, _, run = installation
    (config / '.update-state').mkdir()
    with (config / '.update-state' / 'lock').open('w') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, calls = run()
    assert result.returncode != 0
    assert 'update' in result.stderr.lower()
    assert (config / '.env').read_text() == original
    assert not calls


@pytest.mark.parametrize('name', ['compose.deploy.yml', 'docker-compose.yml'])
def test_compose_mounts_sessions_read_only_without_enabling_login(tmp_path, name):
    if shutil.which('docker') is None:
        pytest.skip('Docker Compose CLI unavailable')
    root = SCRIPT.parents[1]
    shutil.copyfile(root / name, tmp_path / name)
    shutil.copyfile(root / '.env.example', tmp_path / '.env')
    result = subprocess.run(['docker', 'compose', '--project-directory', str(tmp_path),
                             '--env-file', str(tmp_path / '.env'), '-f', str(tmp_path / name),
                             'config', '--format', 'json'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    service = json.loads(result.stdout)['services']['bot']
    mount = next(v for v in service['volumes'] if v['target'] == '/run/secrets')
    assert mount['type'] == 'bind' and mount['read_only'] is True
    assert Path(mount['source']) == tmp_path / 'secrets'
    assert service['environment']['INSTAGRAM_COOKIES_FILE'] == ''
