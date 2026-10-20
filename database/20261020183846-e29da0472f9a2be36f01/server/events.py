import json
import os
import time
from .config import ROOT

# Scheduler emits here; never put append-only events in hashed node inputs.
def activity(node_id='', after=0, limit=100):
    from pathlib import Path
    path = Path(os.environ.get('NGRAM_RUNTIME_DIR', str(Path.home() / '.local/state/ngramdb/runtime'))) / 'events.jsonl'
    if not path.exists():
        return []
    with path.open('rb') as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - 2 * 1024 * 1024))
        if size > 2 * 1024 * 1024:
            f.readline()
        lines = f.readlines()
    result = []
    for line in lines:
        try:
            event = json.loads(line)
            if event['timestamp'] <= after or node_id and event.get('nodeId') != node_id:
                continue
            result.append(event)
        except (ValueError, KeyError):
            continue
    return result[-limit:]

def status(event):
    if not event:
        return 'unknown'
    if event['kind'] == 'started':
        if event.get('host') != __import__('socket').gethostname() or time.time() - event['timestamp'] > 86400:
            return 'stale'
        try:
            os.kill(event['pid'], 0)
            return 'running'
        except (OSError, KeyError):
            return 'stale'
    return 'completed' if event.get('exitCode') == 0 else 'failed'
