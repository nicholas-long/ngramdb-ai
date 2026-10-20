"""Build within the web node; publish the entrypoint after successful validation."""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from .config import WEB, STATE


def main():
    sources = sorted(p for p in (WEB / 'client').rglob('*') if p.is_file() and 'node_modules' not in p.parts and 'dist' not in p.parts)
    digest = hashlib.sha256(b''.join(str(p.relative_to(WEB)).encode() + p.read_bytes() for p in sources)).hexdigest()
    marker = WEB / '.build-hash'
    if marker.exists() and marker.read_text() == digest and (WEB / 'dist/index.html').exists():
        return
    cache = WEB / 'client'
    subprocess.run(['npm', 'ci', '--no-audit', '--no-fund'], cwd=cache, check=True)
    subprocess.run(['npm', 'run', 'build'], cwd=cache, check=True)
    # Keep old hashed assets for already-open browsers; replace the entrypoint last.
    dist = WEB / 'dist'
    dist.mkdir(exist_ok=True)
    for source in sorted((cache / 'dist').rglob('*'), key=lambda p: p.name == 'index.html'):
        if source.is_file():
            target = dist / source.relative_to(cache / 'dist')
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_bytes() != source.read_bytes():
                temporary = target.with_suffix(target.suffix + '.tmp')
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
    marker.write_text(digest)

if __name__ == '__main__':
    main()
