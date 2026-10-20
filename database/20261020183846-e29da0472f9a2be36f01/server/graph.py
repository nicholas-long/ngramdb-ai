import json
import re
import subprocess
import threading
import time
from fastapi import HTTPException
from .config import ROOT
from . import files
from .events import activity, status

ID = re.compile(r'^[0-9]{14}-[0-9a-f]{20}$')
LOCK = threading.Lock()
CACHE = (0, None)

def check_id(value):
    if not ID.fullmatch(value):
        raise HTTPException(400, 'Invalid node ID')
    return value

def cli(*args):
    try:
        proc = subprocess.run([str(ROOT / 'ngram'), *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        raise HTTPException(503, 'Ngram CLI unavailable')
    if proc.returncode not in (0, 1):
        raise HTTPException(503, 'Ngram query failed')
    return sorted(set(line for line in proc.stdout.splitlines() if ID.fullmatch(line)))

def snapshot(force=False):
    global CACHE
    with LOCK:
        if not force and CACHE[1] is not None and time.monotonic() - CACHE[0] < 15:
            return CACHE[1]
        ids = cli('query')
        nodes, edges, allfiles = [], [], []
        for node_id in ids:
            try:
                readme = files.read(node_id, 'README.md').decode('utf8', 'replace')[:files.CAP]
            except HTTPException:
                readme = ''
            try:
                metadata = json.loads(files.read(node_id, 'data.json'))
                if not isinstance(metadata, dict):
                    metadata = {}
            except (HTTPException, ValueError):
                metadata = {}
            try:
                tags = files.read(node_id, 'tags.ngram').decode('utf8', 'replace').splitlines()
            except HTTPException:
                tags = []
            title = next((line.lstrip('# ').strip() for line in readme.splitlines() if line.startswith('#')), metadata.get('title', node_id))
            nodes.append(dict(id=node_id, title=title, tags=tags, links={'self': '/nodes/' + node_id}))
            try:
                allfiles.extend(files.manifest(node_id))
            except HTTPException:
                pass
            dependencies = cli('refs', node_id)
            loose = cli('query', 'id', node_id, 'looserefs')
            for target in sorted(set(dependencies + loose)):
                if target != node_id and target in ids:
                    # A -> B means A depends on / refers to B, matching CLI.
                    edges.append(dict(id=node_id + ':' + target, source=node_id, target=target, kind='dependency' if target in dependencies else 'loose'))
        result = dict(schemaVersion=1, nodes=nodes, edges=edges, files=allfiles)
        CACHE = (time.monotonic(), result)
        return result

def nodes_with_status():
    latest = {}
    for event in activity(limit=10000):
        if event.get('nodeId'):
            latest[event['nodeId']] = event
    return [dict(n, lastRun=latest.get(n['id']), status=status(latest.get(n['id']))) for n in snapshot()['nodes']]

def detail(node_id):
    check_id(node_id)
    node = next((n for n in nodes_with_status() if n['id'] == node_id), None)
    if not node:
        raise HTTPException(404, 'Node not found')
    try:
        metadata = json.loads(files.read(node_id, 'data.json'))
    except (HTTPException, ValueError):
        metadata = {}
    try:
        readme = files.read(node_id, 'README.md').decode('utf8', 'replace')[:files.CAP]
    except HTTPException:
        readme = ''
    return dict(node, metadata=metadata, readme=readme, files=files.listing(node_id), history=activity(node_id, limit=50))
