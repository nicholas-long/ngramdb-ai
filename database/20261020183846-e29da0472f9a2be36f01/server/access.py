"""Private, timestamped request logs; Uvicorn validates trusted proxy headers."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from .config import STATE

LOGGER = logging.getLogger('ngram.web.access')
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


class PrivateRotatingHandler(TimedRotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return os.fdopen(fd, 'a', encoding='utf-8')


def record(request, status):
    try:
        if not LOGGER.handlers:
            STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = STATE / 'access.jsonl'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(fd)
            handler = PrivateRotatingHandler(path, when='midnight', utc=True, backupCount=14, encoding='utf-8', delay=True)
            LOGGER.addHandler(handler)
        LOGGER.info(json.dumps(dict(timestamp=datetime.now(timezone.utc).isoformat(), ip=request.client.host if request.client else '', request=f'{request.method} {request.url.path} HTTP/{request.scope.get("http_version", "1.1")}', status=status), sort_keys=True))
    except OSError:
        logging.getLogger(__name__).exception('Cannot write HTTP access log')
