from .config import STATE, atomic
from .graph import snapshot, file_index
if __name__ == '__main__':
    atomic(STATE / 'graph-index.json', dict(snapshot(force=True), files=file_index(force=True)))
