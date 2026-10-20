"""Durable task intake. The API never starts processes."""
import fcntl
import hashlib
import json
import re
import time
import uuid
from contextlib import contextmanager
from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from .config import STATE, atomic, settings
from .auth import audit

class Submission(BaseModel):
    prompt: str = Field(min_length=1, max_length=16000)
    repository: str = Field(min_length=1, max_length=100)
    mode: Literal['read-only', 'patch'] = 'read-only'

@contextmanager
def locked():
    directory = STATE / 'tasks'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / '.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield directory

def get(task_id, session):
    if not re.fullmatch(r'[0-9a-f]{32}', task_id):
        raise HTTPException(404, 'Task not found')
    try:
        data = json.loads((STATE / 'tasks' / (task_id + '.json')).read_text())
    except FileNotFoundError:
        raise HTTPException(404, 'Task not found')
    if data['owner'] != session['user'] and session['role'] != 'admin':
        raise HTTPException(404, 'Task not found')
    return data

def listing(session, limit=200):
    with locked() as directory:
        tasks = [json.loads(p.read_text()) for p in directory.glob('*.json')]
    result = sorted([t for t in tasks if t['owner'] == session['user'] or session['role'] == 'admin'], key=lambda t: t['created'], reverse=True)
    return result[:limit] if limit is not None else result

def submit(body, session, key):
    if body.repository not in settings().get('repositories', {}):
        raise HTTPException(400, 'Repository is not allowed')
    if not body.prompt.strip():
        raise HTTPException(422, 'Prompt must not be blank')
    if key and (len(key) > 128 or not re.fullmatch(r'[A-Za-z0-9_-]+', key)):
        raise HTTPException(400, 'Invalid idempotency key')
    digest = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
    with locked() as directory:
        tasks = [json.loads(p.read_text()) for p in directory.glob('*.json')]
        for task in tasks:
            if key and task['owner'] == session['user'] and task.get('key') == key:
                if task['digest'] != digest:
                    raise HTTPException(409, 'Idempotency key already used for different input')
                return task
        if sum(t['status'] in ('queued', 'running') for t in tasks) >= 20:
            raise HTTPException(429, 'Task queue is full')
        task = dict(id=uuid.uuid4().hex, owner=session['user'], created=time.time(),
                    status='queued', key=key, digest=digest, **body.model_dump())
        atomic(directory / (task['id'] + '.json'), task)
    audit('task_submitted', session['user'], taskId=task['id'])
    return task

def cancel(task_id, session):
    with locked() as directory:
        task = get(task_id, session)
        if task['status'] == 'queued':
            task['status'] = 'cancelled'
        elif task['status'] == 'running':
            task['cancelRequested'] = True
        atomic(directory / (task_id + '.json'), task)
    audit('task_cancelled', session['user'], taskId=task_id)
    return task

def review(task_id, session):
    with locked() as directory:
        task = get(task_id, session)
        if task['status'] != 'needs-review':
            raise HTTPException(409, 'Task is not awaiting review')
        task.update(reviewedBy=session['user'], reviewedAt=time.time(), status='completed')
        atomic(directory / (task_id + '.json'), task)
    audit('task_reviewed', session['user'], taskId=task_id)
    return task

def output(task_id, session, name):
    get(task_id, session)
    path = STATE / 'task-output' / task_id / name
    if not path.exists():
        return ''
    with path.open('rb') as f:
        f.seek(max(0, path.stat().st_size - 256 * 1024))
        return f.read(256 * 1024).decode('utf8', 'replace')
