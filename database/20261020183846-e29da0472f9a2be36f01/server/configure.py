"""Interactive local setup; credentials are never stored in database nodes."""
import argparse
import getpass
import os
from pathlib import Path
from argon2 import PasswordHasher
from .config import STATE, CONFIG, atomic, settings

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('username')
    parser.add_argument('--role', choices=['viewer','operator','admin'], default='viewer')
    parser.add_argument('--repository', action='append', default=[], metavar='NAME=PATH')
    parser.add_argument('--insecure-local', action='store_true', help='Allow HTTP cookies for localhost development only')
    parser.add_argument('--origin', default='https://localhost')
    args = parser.parse_args()
    password = getpass.getpass('New password (at least 12 characters): ')
    if len(password) < 12 or password != getpass.getpass('Repeat password: '):
        raise SystemExit('Passwords must match and contain at least 12 characters')
    config = settings()
    config.setdefault('users', {})[args.username] = {'passwordHash': PasswordHasher().hash(password), 'role': args.role}
    for spec in args.repository:
        name, path = spec.split('=',1)
        config.setdefault('repositories', {})[name] = str(Path(path).resolve(strict=True))
    config.update(secureCookies=not args.insecure_local, origin=args.origin)
    atomic(CONFIG, config)
    os.chmod(CONFIG, 0o600)
    print('Saved private configuration to', CONFIG)

if __name__ == '__main__':
    main()
