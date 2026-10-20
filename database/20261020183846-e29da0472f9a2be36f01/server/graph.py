"""Independent metadata, file-stat, and relationship indexes.

Node lists must never build graph relationships or read generated data files.
"""
import json
import os
import re
import signal
import subprocess
import threading
import time
from fastapi import HTTPException
from .config import ROOT
from . import files
from .events import activity, status

ID = re.compile(r'^[0-9]{14}-[0-9a-f]{20}$')
LOCK = threading.Lock()
NODE_LOCK = threading.Lock()
FILE_LOCK = threading.Lock()
LOOSE_LOCK = threading.Lock()
CACHE = (0, None)
NODE_CACHE = (0, None)
FILE_CACHE = (0, None)
LOOSE_CACHE = (0, None, None)
TTL = 15


def check_id(value):
    if not ID.fullmatch(value):
        raise HTTPException(400, 'Invalid node ID')
    return value


def cli(*args):
    try:
        proc = subprocess.Popen([str(ROOT / 'ngram'), *args], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
    except OSError:
        raise HTTPException(503, 'Ngram CLI unavailable')
    try:
        output, _ = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        # Killing only the shell leaves grep/find children scanning in the background.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise HTTPException(503, 'Ngram query timed out')
    if proc.returncode not in (0, 1):
        raise HTTPException(503, 'Ngram query failed')
    return sorted(set(line for line in output.splitlines() if ID.fullmatch(line)))


def node_index(force=False):
    global NODE_CACHE
    with NODE_LOCK:
        if not force and NODE_CACHE[1] is not None and time.monotonic() - NODE_CACHE[0] < TTL:
            return NODE_CACHE[1]
        nodes = []
        # One cheap CLI discovery, followed only by bounded metadata reads.
        for node_id in cli('query'):
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
        NODE_CACHE = (time.monotonic(), nodes)
        return nodes


def file_index(force=False):
    global FILE_CACHE
    with FILE_LOCK:
        if not force and FILE_CACHE[1] is not None and time.monotonic() - FILE_CACHE[0] < TTL:
            return FILE_CACHE[1]
        result = []
        for node in node_index():
            try:
                # Manifest reads filenames/stat metadata, never file contents.
                result.extend(files.manifest(node['id']))
            except HTTPException:
                pass
        FILE_CACHE = (time.monotonic(), result)
        return result


def snapshot(force=False, include_loose=False):
    global CACHE, LOOSE_CACHE
    with LOCK:
        if not force and CACHE[1] is not None and time.monotonic() - CACHE[0] < TTL:
            result = CACHE[1]
        else:
            nodes = node_index(force=force)
            ids = {node['id'] for node in nodes}
            edges = []
            for node_id in sorted(ids):
                for target in cli('refs', node_id):
                    if target != node_id and target in ids:
                        edges.append(dict(id=node_id + ':' + target, source=node_id, target=target, kind='dependency'))
            result = dict(schemaVersion=1, nodes=nodes, edges=edges)
            CACHE = (time.monotonic(), result)
    if not include_loose:
        return result
    # Loose references are explicitly opt-in and cannot block the ordinary graph.
    with LOOSE_LOCK:
        if not force and LOOSE_CACHE[1] is result and time.monotonic() - LOOSE_CACHE[0] < TTL:
            return LOOSE_CACHE[2]
        ids = {node['id'] for node in result['nodes']}
        edges = list(result['edges'])
        existing = {(edge['source'], edge['target']) for edge in edges}
        for node_id in sorted(ids):
            for target in cli('docrefs', node_id):
                if target != node_id and target in ids and (node_id, target) not in existing:
                    edges.append(dict(id=node_id + ':' + target, source=node_id, target=target, kind='loose'))
        enriched = dict(result, edges=edges)
        LOOSE_CACHE = (time.monotonic(), result, enriched)
        return enriched


def nodes_with_status():
    nodes = node_index()
    latest = {}
    for event in activity(limit=10000):
        if event.get('nodeId'):
            latest[event['nodeId']] = event
    return [dict(n, lastRun=latest.get(n['id']), status=status(latest.get(n['id']))) for n in nodes]


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
