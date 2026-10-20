import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException, Request
from .config import STATE, settings

COOKIE = 'ngram_session'
LOCK = threading.RLock()
SESSIONS = {}
ATTEMPTS = defaultdict(deque)
DUMMY = PasswordHasher().hash(secrets.token_urlsafe(32))

def audit(kind, user, **fields):
    import json
    import os
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK:
        fd = os.open(STATE / 'audit.jsonl', os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as out:
            out.write(json.dumps(dict(timestamp=time.time(), kind=kind, user=user, **fields)) + '\n')

def rate(key, maximum):
    now = time.time()
    with LOCK:
        # Drop inactive buckets so changing IPs cannot grow state forever.
        for old in list(ATTEMPTS):
            if not ATTEMPTS[old] or ATTEMPTS[old][-1] < now - 60:
                del ATTEMPTS[old]
        bucket = ATTEMPTS[key]
        if len(bucket) >= maximum:
            raise HTTPException(429, 'Try again in a minute')
        bucket.append(now)

def login(username, password, request):
    rate('login:' + request.client.host, 8)
    config = settings()
    user = config.get('users', {}).get(username)
    try:
        valid = PasswordHasher().verify(user['passwordHash'] if user else DUMMY, password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid or not user:
        audit('login_failed', username)
        raise HTTPException(401, 'Invalid username or password')
    token = secrets.token_urlsafe(32)
    session = dict(user=username, role=user.get('role', 'viewer'), csrf=secrets.token_urlsafe(32), expires=time.time() + 28800)
    with LOCK:
        for key in list(SESSIONS):
            if SESSIONS[key]['expires'] <= time.time():
                del SESSIONS[key]
        SESSIONS.pop(request.cookies.get(COOKIE), None)
        SESSIONS[token] = session
    audit('login', username)
    return token, session

def identity(request: Request):
    with LOCK:
        session = SESSIONS.get(request.cookies.get(COOKIE))
    if not session or session['expires'] <= time.time():
        raise HTTPException(401, 'Please sign in')
    # Disabling a user or changing a role takes effect immediately.
    user = settings().get('users', {}).get(session['user'])
    if not user:
        raise HTTPException(401, 'Account disabled')
    session = dict(session, role=user.get('role', 'viewer'))
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), session['csrf']):
            raise HTTPException(403, 'CSRF check failed')
    return session

def operator(request: Request):
    session = identity(request)
    if session['role'] not in ('operator', 'admin'):
        raise HTTPException(403, 'Task permission required')
    return session
