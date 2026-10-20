"""One opt-in worker, isolated patch worktrees, bounded execution, no HTTP calls."""
import fcntl
import json
import os
import re
import signal
import subprocess
import time
import tempfile
from pathlib import Path
from .config import STATE, atomic, settings
from . import tasks

SYSTEM = {'user': '', 'role': 'admin'}

def git(repo, *args, env=None):
    return subprocess.run(['git', '-C', str(repo), *args], env=env, check=True, capture_output=True, timeout=60).stdout.decode('utf8', 'replace')

def finish(task, **fields):
    with tasks.locked() as directory:
        current = tasks.get(task['id'], SYSTEM)
        current.update(fields)
        atomic(directory / (task['id'] + '.json'), current)

def task_environment(root):
    # Ngram commands launched by a task must resolve to that task's checkout.
    return dict(os.environ, PATH=str(root) + os.pathsep + os.environ.get('PATH', ''), NGRAM_ROOT=str(root))


def snapshot_worktree(repo, work):
    """Include live tracked/untracked source without touching the real Git index or HEAD."""
    with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=STATE) as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / 'index'))
        git(repo, 'read-tree', 'HEAD', env=env)
        git(repo, 'add', '-A', '--', '.', env=env)
        tree = git(repo, 'write-tree', env=env).strip()
    git(repo, 'worktree', 'add', '--detach', str(work), 'HEAD')
    git(work, 'read-tree', '--reset', '-u', tree)
    return tree


def publish_nodes(task, repo, work, base_revision=None):
    """Copy changed node sources back, creating or updating their live directories."""
    expected = STATE / 'worktrees' / task['id']
    if task['mode'] != 'patch' or work.resolve() != expected:
        raise ValueError('Node publication requires this task\'s patch worktree')
    revision = base_revision or task.get('baseRevision') or 'HEAD'
    changed = git(work, 'diff', '--name-only', '--no-ext-diff', '-z', revision).split('\0')
    pattern = re.compile(r'[0-9]{14}-[0-9a-f]{20}')
    candidates = sorted({path.split('/')[1] for path in changed if path.startswith('database/') and len(path.split('/')) >= 3 and pattern.fullmatch(path.split('/')[1])})
    recorded = tasks.get(task['id'], SYSTEM)
    imported = list(recorded.get('importedNodeIds') or [])
    updated = list(recorded.get('updatedNodeIds') or [])
    published = []
    for node_id in candidates:
        source = work / 'database' / node_id
        if not source.is_dir():
            continue  # Deletions remain proposals; publication never deletes live nodes.
        existed = (repo / 'database' / node_id).exists()
        command = [str(repo / 'ngram'), 'import-node', '--overwrite', str(source)]
        result = subprocess.run(command, cwd=repo, env=task_environment(repo), capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError('Node import failed: ' + result.stderr.strip()[-2000:])
        published.append(node_id)
        if node_id not in imported and node_id not in updated:
            (updated if existed else imported).append(node_id)
        finish(task, importedNodeIds=imported, updatedNodeIds=updated, publishedNodeIds=published)
    return published


def execute(task):
    output = STATE / 'task-output' / task['id']
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    config = settings()
    repo = Path(config['repositories'][task['repository']]).resolve(strict=True)
    work = repo
    base_revision = None
    if task['mode'] == 'patch':
        work = STATE / 'worktrees' / task['id']
        work.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        base_revision = snapshot_worktree(repo, work)
    baseline = {p.name for p in (work / 'database').iterdir()} if (work / 'database').is_dir() else set()
    command = ['codex', 'exec', '--json', '--ignore-user-config', '--ephemeral',
               '--sandbox', 'read-only' if task['mode'] == 'read-only' else 'workspace-write',
               '-C', str(work), '--output-last-message', str(output / 'result.txt'), '-']
    timeout = min(int(config.get('taskTimeout', 600)), 3600)
    with (output / 'events.jsonl').open('wb') as log:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=task_environment(work))
        prompt = task['prompt']
        if task['mode'] == 'patch':
            prompt += '\n\nThis worktree includes the current live source files, including uncommitted nodes. Create new Ngram nodes with ./ngram create and edit existing nodes here. After a successful task, changed database/<ID>/ source files are copied back to the live database, overwriting existing source files while preserving generated outputs and logs. Other repository changes remain proposals for review. Use this checkout for all Ngram commands. Task baseline tree: ' + base_revision
        proc.stdin.write(prompt.encode())
        proc.stdin.close()
        finish(task, pid=proc.pid, started=time.time(), worktree=str(work) if work != repo else None, baseRevision=base_revision, baseNodeIds=sorted(baseline), importedNodeIds=[], updatedNodeIds=[], publishedNodeIds=[])
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
            (output / 'changes.diff').write_text(git(work, 'diff', '--no-ext-diff', base_revision))
        finish(task, exitCode=proc.returncode)
        published = []
        remaining = []
        if not reason and proc.returncode == 0 and task['mode'] == 'patch':
            if tasks.get(task['id'], SYSTEM).get('cancelRequested'):
                reason = 'cancelled'
            else:
                published = publish_nodes(task, repo, work, base_revision)
                changed = git(work, 'diff', '--name-only', '--no-ext-diff', '-z', base_revision).split('\0')
                remaining = [path for path in changed if path and not any(path.startswith('database/' + node_id + '/') for node_id in published)]
        status = reason or ('failed' if proc.returncode else 'needs-review' if task['mode'] == 'patch' and remaining else 'completed')
        finish(task, status=status, exitCode=proc.returncode, finished=time.time(), publishedNodeIds=published, error='Timeout or output limit exceeded' if reason == 'failed' else None)

def main():
    if os.environ.get('NGRAM_CODEX_WORKER_ENABLED') != '1':
        print('Codex worker disabled; set NGRAM_CODEX_WORKER_ENABLED=1 to enable it.')
        return
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
