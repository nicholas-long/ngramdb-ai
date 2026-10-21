"""Private file policy. No symlinks, hidden paths, credentials, or active previews."""
import csv
import gzip
import zlib
import io
import mimetypes
import os
import stat
from pathlib import Path, PurePosixPath
from fastapi import HTTPException
from .config import ROOT

CAP = 256 * 1024
DENIED = {'node_modules', 'dist', 'venv', '__pycache__', 'credentials', 'config.json',
          'auth.json', 'id_rsa', 'id_ed25519', 'package-lock.json'}
SECRET_SUFFIXES = ('.pem', '.key', '.p12', '.pfx', '.sqlite', '.db')

def parts(path):
    if '\x00' in path or '\\' in path or path.startswith('/'):
        raise HTTPException(400, 'Invalid path')
    bits = PurePosixPath(path).parts
    if any(p in ('..', '.') or p.startswith('.') or p.lower() in DENIED or
           p.lower().endswith(SECRET_SUFFIXES) or any(s in p.lower() for s in ('secret', 'credential', 'token', 'password')) for p in bits):
        raise HTTPException(403, 'File is private')
    return bits

def open_safe(node_id, path='', directory=False, base=None):
    """Walk with openat + O_NOFOLLOW to avoid symlink swaps, including ancestor dirs."""
    bits = parts(path)
    if len(parts(node_id)) != 1 or '/' in node_id:
        raise HTTPException(400, 'Invalid root')
    root = base or ROOT / 'database'
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise HTTPException(404, 'Root unavailable')
    try:
        for part in (node_id, *bits):
            nxt = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            os.close(fd)
            fd = nxt
        mode = os.fstat(fd).st_mode
        if directory and not stat.S_ISDIR(mode) or not directory and not stat.S_ISREG(mode):
            raise HTTPException(404, 'Not a regular file or directory')
        return fd
    except (OSError, ValueError):
        os.close(fd)
        raise HTTPException(404, 'File unavailable')
    except Exception:
        os.close(fd)
        raise

def read(node_id, path, limit=CAP):
    with os.fdopen(open_safe(node_id, path), 'rb') as f:
        return f.read(limit + 1)

def info(path, st):
    ext = Path(path).suffix.lower()
    mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    kind = 'markdown' if ext == '.md' else 'json' if ext in ('.json', '.jsonl') else 'csv' if ext == '.csv' else 'image' if ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp') else 'text' if ext in ('.txt', '.log', '.py', '.sh', '.ts', '.tsx', '.css', '.ngram') or not ext else 'binary'
    return {'path': path, 'extension': ext, 'mime': mime, 'size': st.st_size, 'modified': st.st_mtime, 'kind': kind, 'directory': stat.S_ISDIR(st.st_mode)}

def listing(node_id, path=''):
    fd = open_safe(node_id, path, directory=True)
    try:
        result = []
        for name in sorted(os.listdir(fd))[:5000]:
            relative = str(PurePosixPath(path) / name)
            try:
                parts(relative)
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode):
                    result.append(info(relative, st))
            except (HTTPException, OSError):
                continue
        return result
    finally:
        os.close(fd)

def manifest(node_id, path='', depth=0):
    if depth > 8:
        return []
    result = []
    for item in listing(node_id, path):
        if item['directory']:
            result.extend(manifest(node_id, item['path'], depth + 1))
        else:
            result.append(dict(item, nodeId=node_id))
        if len(result) >= 10000:
            return result[:10000]
    return result

def preview(node_id, path):
    compressed = Path(path).suffix.lower() == '.gz'
    try:
        with os.fdopen(open_safe(node_id, path), 'rb') as source:
            if compressed:
                with gzip.GzipFile(fileobj=source) as decoded:
                    data = decoded.read(CAP + 1)
            else:
                data = source.read(CAP + 1)
    except (OSError, EOFError, zlib.error):
        raise HTTPException(422, 'Invalid or damaged gzip file')
    kind = info(path[:-3] if compressed else path, os.stat_result((0,) * 10))['kind']
    # Compressed images cannot use the raw image URL; keep them downloadable.
    if compressed and kind == 'image':
        kind = 'binary'
    text = data[:CAP].decode('utf-8', errors='replace')
    result = {'path': path, 'kind': kind, 'truncated': len(data) > CAP, 'content': text}
    if kind == 'csv':
        result['rows'] = list(__import__('itertools').islice(csv.reader(io.StringIO(text)), 101))
    if kind in ('binary', 'image'):
        result['content'] = ''
    return result
