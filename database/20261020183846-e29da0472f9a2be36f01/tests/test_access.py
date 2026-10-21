"""Standard-library checks for private request log rotation."""
import json
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from server import access


class AccessLogTests(unittest.TestCase):
    def test_records_and_private_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            logger = logging.Logger('access-test', logging.INFO)
            request = SimpleNamespace(client=SimpleNamespace(host='2001:db8::1'), method='GET', url=SimpleNamespace(path='/hello'), scope={'http_version': '1.1'})
            with patch.object(access, 'STATE', state), patch.object(access, 'LOGGER', logger):
                try:
                    access.record(request, 200)
                    row = json.loads((state / 'access.jsonl').read_text())
                    self.assertEqual(row['ip'], '2001:db8::1')
                    self.assertEqual(row['request'], 'GET /hello HTTP/1.1')
                    self.assertEqual(row['status'], 200)
                    self.assertTrue(row['timestamp'].endswith('+00:00'))
                    logger.handlers[0].doRollover()
                    access.record(request, 403)
                    self.assertEqual(json.loads((state / 'access.jsonl').read_text())['status'], 403)
                    logs = list(state.glob('access.jsonl*'))
                    self.assertEqual(len(logs), 2)
                    for path in logs:
                        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                finally:
                    for handler in logger.handlers:
                        handler.close()


if __name__ == '__main__':
    unittest.main()
