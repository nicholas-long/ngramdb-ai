#!/usr/bin/env python3
"""Best-effort structured telemetry; never writes into dependency-tracked nodes."""
import json
import os
import socket
import sys
import time
from pathlib import Path
try:
    node, run, kind, pid, *rest = sys.argv[1:]
    path = Path(os.environ.get('NGRAM_RUNTIME_DIR', str(Path.home() / '.local/state/ngramdb/runtime')))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    event = dict(timestamp=time.time(), nodeId=node, runId=run, kind=kind, pid=int(pid), host=socket.gethostname())
    if rest:
        event.update(exitCode=int(rest[0]), duration=int(rest[1]))
    fd = os.open(path / 'events.jsonl', os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, (json.dumps(event) + '\n').encode())
    finally:
        os.close(fd)
except (OSError, ValueError, KeyError) as exc:
    print('Ngram telemetry unavailable:', exc, file=sys.stderr)
