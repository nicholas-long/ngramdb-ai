import asyncio
import fnmatch
import json
import os
import time
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from . import auth, files, graph, tasks
from .config import WEB, STATE, settings
from .events import activity

class Credentials(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=1024)

def page(items, limit, cursor):
    return dict(items=items[cursor:cursor + limit], total=len(items), nextCursor=cursor + limit if cursor + limit < len(items) else None)

def create_app():
    app = FastAPI(title='NgramDB Web', docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def security(request, call_next):
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            origin = request.headers.get('origin')
            expected = settings().get('origin', str(request.base_url).rstrip('/'))
            if origin and origin != expected:
                return Response('Origin rejected', status_code=403)
            try:
                if int(request.headers.get('content-length', '0')) > 32768:
                    return Response('Request too large', status_code=413)
            except ValueError:
                return Response(status_code=400)
            # Also bound streamed/chunked request bodies.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 32768:
                    return Response('Request too large', status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api') else 'no-cache'
        return response

    @app.get('/api/health')
    def health():
        return {'ok': True, 'schemaVersion': 1}

    @app.post('/api/login')
    def login(body: Credentials, request: Request, response: Response):
        token, session = auth.login(body.username, body.password, request)
        response.set_cookie(auth.COOKIE, token, httponly=True, secure=settings().get('secureCookies', True), samesite='strict', max_age=28800)
        return session

    @app.post('/api/logout')
    def logout(request: Request, response: Response, me=Depends(auth.identity)):
        with auth.LOCK:
            auth.SESSIONS.pop(request.cookies.get(auth.COOKIE), None)
        response.delete_cookie(auth.COOKIE)
        return {'ok': True}

    @app.get('/api/me')
    def me(session=Depends(auth.identity)):
        return dict(session, repositories=list(settings().get('repositories', {})))

    @app.get('/api/nodes')
    def nodes(q: str = '', tag: str = '', sort: str = 'title', limit: int = Query(50, ge=1, le=200), cursor: int = Query(0, ge=0), session=Depends(auth.identity)):
        data = [n for n in graph.nodes_with_status() if (not tag or tag in n['tags']) and q.lower() in (' '.join([n['id'], n['title'], *n['tags']])).lower()]
        data.sort(key=lambda n: n['id'] if sort == 'id' else n['title'].lower())
        return page(data, limit, cursor)

    @app.get('/api/nodes/{node_id}')
    def node(node_id: str, session=Depends(auth.identity)):
        return graph.detail(node_id)

    @app.get('/api/nodes/{node_id}/refs')
    def refs(node_id: str, includeLoose: bool = False, session=Depends(auth.identity)):
        graph.check_id(node_id)
        return [e for e in graph.snapshot(include_loose=includeLoose)['edges'] if node_id in (e['source'], e['target'])]

    @app.get('/api/nodes/{node_id}/files')
    def node_files(node_id: str, path: str = '', session=Depends(auth.identity)):
        return files.listing(graph.check_id(node_id), path)

    @app.get('/api/nodes/{node_id}/file')
    def node_file(node_id: str, path: str, download: bool = False, image: bool = False, session=Depends(auth.identity)):
        graph.check_id(node_id)
        if download or image:
            fd = files.open_safe(node_id, path)
            kind = files.info(path, os.fstat(fd))
            def stream():
                with os.fdopen(fd, 'rb') as f:
                    while block := f.read(65536):
                        yield block
            inline = image and kind['kind'] == 'image'
            from urllib.parse import quote
            return StreamingResponse(stream(), media_type=kind['mime'] if inline else 'application/octet-stream', headers={'Content-Disposition': ('inline' if inline else 'attachment') + "; filename*=UTF-8''" + quote(Path(path).name)})
        return files.preview(node_id, path)

    @app.get('/api/files')
    def all_files(extension: str = '', mime: str = '', q: str = '', tag: str = '', sort: str = 'path', limit: int = Query(50, ge=1, le=200), cursor: int = Query(0, ge=0), session=Depends(auth.identity)):
        ids = {n['id'] for n in graph.node_index() if not tag or tag in n['tags']}
        data = [f for f in graph.file_index() if f['nodeId'] in ids and (not extension or f['extension'] == '.' + extension.lstrip('.').lower()) and (not mime or f['mime'].startswith(mime)) and (not q or q.lower() in f['path'].lower() or fnmatch.fnmatch(f['path'].lower(), q.lower()))]
        data.sort(key=lambda f: f.get(sort, f['path']) if sort in ('size', 'modified') else f['path'])
        return page(data, limit, cursor)

    @app.get('/api/graph')
    def topology(includeLoose: bool = False, tag: str = '', focus: str = '', q: str = '', direction: Literal['both','upstream','downstream'] = 'both', depth: int = Query(1, ge=0, le=5), session=Depends(auth.identity)):
        snap = graph.snapshot(include_loose=includeLoose)
        nodes = graph.nodes_with_status()
        ids = {n['id'] for n in nodes if (not tag or tag in n['tags']) and q.lower() in (n['title'] + ' ' + n['id']).lower()}
        if focus:
            graph.check_id(focus)
            neighborhood = {focus}
            for _ in range(depth):
                neighborhood |= {e[k] for e in snap['edges'] if (direction in ('both','upstream') and e['source'] in neighborhood) or (direction in ('both','downstream') and e['target'] in neighborhood) for k in ('source', 'target')}
            ids &= neighborhood
        return dict(nodes=[n for n in nodes if n['id'] in ids], edges=[e for e in snap['edges'] if e['source'] in ids and e['target'] in ids])

    @app.get('/api/activity')
    def events(nodeId: str = '', after: float = 0, limit: int = Query(100, ge=1, le=500), session=Depends(auth.identity)):
        return activity(nodeId, after, limit)

    @app.get('/api/events')
    async def sse(request: Request, session=Depends(auth.identity)):
        async def stream():
            last = time.time()
            for _ in range(150):
                if await request.is_disconnected():
                    break
                try:
                    auth.identity(request)
                except HTTPException:
                    break
                batch = activity(after=last, limit=500)
                if batch:
                    last = max(e['timestamp'] for e in batch)
                    yield 'event: activity\ndata: ' + json.dumps(batch) + '\n\n'
                else:
                    yield ': heartbeat\n\n'
                await asyncio.sleep(2)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @app.get('/api/tasks')
    def task_list(session=Depends(auth.operator)):
        return tasks.listing(session)

    @app.post('/api/tasks', status_code=201)
    def task_submit(body: tasks.Submission, request: Request, session=Depends(auth.operator)):
        auth.rate('task:' + session['user'], 10)
        return tasks.submit(body, session, request.headers.get('idempotency-key', ''))

    @app.get('/api/tasks/{task_id}')
    def task_detail(task_id: str, session=Depends(auth.operator)):
        return dict(tasks.get(task_id, session), result=tasks.output(task_id, session, 'result.txt'), diff=tasks.output(task_id, session, 'changes.diff'))

    @app.get('/api/tasks/{task_id}/events')
    def task_events(task_id: str, session=Depends(auth.operator)):
        return {'content': tasks.output(task_id, session, 'events.jsonl')}

    @app.post('/api/tasks/{task_id}/cancel')
    def task_cancel(task_id: str, session=Depends(auth.operator)):
        return tasks.cancel(task_id, session)

    @app.post('/api/tasks/{task_id}/review')
    def task_review(task_id: str, session=Depends(auth.operator)):
        return tasks.review(task_id, session)

    @app.get('/public/{path:path}')
    def public(path: str):
        # Explicit publication namespace only. Active content is always an attachment.
        parent, _, name = path.partition('/')
        fd = files.open_safe(parent, name, base=STATE / 'public')
        def stream():
            with os.fdopen(fd, 'rb') as f:
                while chunk := f.read(65536):
                    yield chunk
        return StreamingResponse(stream(), media_type='application/octet-stream', headers={'Content-Disposition': 'attachment'})

    @app.get('/{path:path}')
    def spa(path: str):
        if path.startswith('api/'):
            raise HTTPException(404)
        dist = WEB / 'dist'
        candidate = (dist / path).resolve()
        if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        if (dist / 'index.html').is_file():
            return FileResponse(dist / 'index.html')
        return Response('Build the web node first (see its README).', status_code=503)
    return app

app = create_app()
