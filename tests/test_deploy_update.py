"""Exercise the real updater without Docker, network, systemd or Telegram."""
import json
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/update.sh"


@pytest.fixture
def host(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['FAKE_HOST'])
s = json.loads((p/'state.json').read_text())
a = sys.argv[1:]
with (p/'calls.jsonl').open('a') as f:
    f.write(json.dumps(a)+'\\n')
if a[0] == 'compose':
    a = a[a.index('--env-file')+2:]
    if a[0] == 'pull': sys.exit(s.get('pull_failure', 0))
    if a[0] == 'ps': print('container' if s.get('old') else '')
    if a[0] == 'run':
        assert '-e' in a and a[a.index('-e')+1] == 'BOT_TOKEN='
        assert a[-4:] == ['python', '-m', 'bot.main', '--check']
        sys.exit(s.get('check_failure', 0))
    if a[0] == 'up':
        target = os.environ.get('BOT_IMAGE', s['new'])
        s['active'] = target
        s.setdefault('started', []).append(target)
        (p/'state.json').write_text(json.dumps(s))
        sys.exit(s.get('up_failure', 0) if target == s['new'] else 0)
    if a[0] != 'ps': raise SystemExit('Unexpected compose command: ' + repr(a))
elif a[0:2] == ['image','inspect']: print(s['new'])
elif a[0:2] == ['image','tag']: pass
elif a[0] == 'inspect':
    if '.Image' in a[2]: print(s['old'])
    else: print('exited 1' if s.get('bad_start') and s.get('active') == s['new'] else s.get('status', 'running 0'))
else: raise SystemExit('Unexpected docker command: ' + repr(a))
''')
    # macOS lacks flock; locking itself is provided by util-linux on the target.
    (bindir / "flock").write_text("#!/bin/sh\nexit 0\n")
    (bindir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for entry in bindir.iterdir():
        entry.chmod(0o755)
    config = tmp_path / "config"
    config.mkdir()
    (config / ".env").write_text("BOT_TOKEN=\n")
    (config / "compose.deploy.yml").touch()
    state = {"new": "sha256:new", "old": "sha256:old"}

    def run(**changes):
        state.update(changes)
        (tmp_path / "state.json").write_text(json.dumps(state))
        env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'], FAKE_HOST=str(tmp_path))
        result = subprocess.run(["bash", str(SCRIPT), str(config)], env=env, capture_output=True, text=True)
        state.update(json.loads((tmp_path / "state.json").read_text()))
        return result

    return run, state, config


def test_unchanged_image_does_not_restart(host):
    run, state, _ = host
    assert run(new="sha256:old").returncode == 0
    assert not state.get('started')


@pytest.mark.parametrize('failure', ['pull_failure', 'check_failure'])
def test_failed_preparation_keeps_running_container(host, failure):
    run, state, _ = host
    result = run(**{failure: 1})
    assert result.returncode != 0
    assert 'existing bot retained' in result.stderr
    assert not state.get('started')


def test_healthy_update_uses_exact_pulled_image(host):
    run, state, _ = host
    assert run().returncode == 0
    assert state['started'] == ['sha256:new']


@pytest.mark.parametrize('failure', ['bad_start', 'up_failure'])
def test_failed_start_rolls_back_and_does_not_retry_same_image(host, failure):
    run, state, _ = host
    assert run(**{failure: 1}).returncode != 0
    assert state['started'] == ['sha256:new', 'sha256:old']
    assert run().returncode == 0
    assert state['started'] == ['sha256:new', 'sha256:old']
    assert run(new='sha256:fixed', bad_start=False, up_failure=0).returncode == 0
    assert state['started'][-1] == 'sha256:fixed'


def test_stopped_container_is_not_started_by_timer(host):
    run, state, _ = host
    assert run(status='exited 0').returncode == 0
    assert not state.get('started')


def test_timer_does_not_perform_first_install(host):
    run, state, _ = host
    assert run(old='').returncode == 0
    assert not state.get('started')
