import json
import os
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get('NGRAM_ROOT', WEB.parents[1])).resolve()
STATE = Path(os.environ.get('NGRAM_WEB_STATE', str(Path.home() / '.local/state/ngram-web'))).resolve()
CONFIG = Path(os.environ.get('NGRAM_WEB_CONFIG', str(STATE / 'config.json')))

def settings():
    if not CONFIG.is_file():
        return {'users': {}, 'repositories': {}}
    return json.loads(CONFIG.read_text())

def atomic(path, data):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = json.dumps(data, sort_keys=True, indent=2) + '\n'
    if path.exists() and path.read_text() == content:
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
