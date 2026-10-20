"""One opt-in worker, isolated patch worktrees, bounded execution, no HTTP calls."""
import fcntl
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from .config import STATE, atomic, settings
from . import tasks

SYSTEM = {'user': '', 'role': 'admin'}

def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, timeout=60).stdout.decode('utf8', 'replace')

def finish(task, **fields):
    with tasks.locked() as directory:
        current = tasks.get(task['id'], SYSTEM)
        current.update(fields)
        atomic(directory / (task['id'] + '.json'), current)

def execute(task):
    output = STATE / 'task-output' / task['id']
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    config = settings()
    repo = Path(config['repositories'][task['repository']]).resolve(strict=True)
    work = repo
    if task['mode'] == 'patch':
        work = STATE / 'worktrees' / task['id']
        work.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        git(repo, 'worktree', 'add', '--detach', str(work), 'HEAD')
    command = ['codex', 'exec', '--json', '--ignore-user-config', '--ephemeral',
               '--sandbox', 'read-only' if task['mode'] == 'read-only' else 'workspace-write',
               '-C', str(work), '--output-last-message', str(output / 'result.txt'), '-']
    timeout = min(int(config.get('taskTimeout', 600)), 3600)
    with (output / 'events.jsonl').open('wb') as log:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        proc.stdin.write(task['prompt'].encode())
        proc.stdin.close()
        finish(task, pid=proc.pid, started=time.time(), worktree=str(work) if work != repo else None)
        end = time.monotonic() + timeout
        reason = None
        try:
            while proc.poll() is None:
                if tasks.get(task['id'], SYSTEM).get('cancelRequested'):
                    reason = 'cancelled'
                elif time.monotonic() > end:
                    reason = 'failed'
                elif log.tell() > 10 * 1024 * 1024:
                    reason = 'failed'
                if reason:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                    break
                time.sleep(0.5)
            proc.wait()
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        if task['mode'] == 'patch':
            # Include untracked proposals in the review diff without touching the source repo.
            git(work, 'add', '-N', '.')
            (output / 'changes.diff').write_text(git(work, 'diff', '--no-ext-diff', 'HEAD'))
        finish(task, status=reason or ('needs-review' if task['mode'] == 'patch' and proc.returncode == 0 else 'completed' if proc.returncode == 0 else 'failed'), exitCode=proc.returncode, finished=time.time(), error='Timeout or output limit exceeded' if reason == 'failed' else None)

def main():
    if os.environ.get('NGRAM_CODEX_WORKER_ENABLED') != '1':
        print('Codex worker disabled; set NGRAM_CODEX_WORKER_ENABLED=1 under a dedicated account.')
        return
    policy = settings()
    if policy.get('workerUid') != os.getuid() or policy.get('webUid') in (None, os.getuid()) or os.getuid() == 0:
        raise SystemExit('Configure distinct webUid and workerUid; run the worker as workerUid (non-root)')
    def stop(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (STATE / 'worker.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('A worker is already running')
    # A previous worker crash must not leave tasks indefinitely running or replay prompts.
    for task in tasks.listing(SYSTEM, limit=None):
        if task['status'] == 'running':
            finish(task, status='failed', error='Worker interrupted; review partial output before resubmitting')
    while True:
        queued = sorted((t for t in tasks.listing(SYSTEM, limit=None) if t['status'] == 'queued'), key=lambda t: t['created'])
        if not queued:
            time.sleep(2)
            continue
        task = queued[0]
        with tasks.locked() as directory:
            current = tasks.get(task['id'], SYSTEM)
            if current['status'] != 'queued':
                continue
            current['status'] = 'running'
            atomic(directory / (task['id'] + '.json'), current)
        try:
            execute(task)
        except Exception as exc:
            finish(task, status='failed', finished=time.time(), error=str(exc)[:1000])

if __name__ == '__main__':
    main()
