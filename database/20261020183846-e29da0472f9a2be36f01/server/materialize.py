from .config import STATE, atomic
from .graph import snapshot
if __name__ == '__main__':
    atomic(STATE / 'graph-index.json', snapshot(force=True))
