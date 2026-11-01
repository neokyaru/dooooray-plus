#!/usr/bin/env python3
"""Validate and publish the public Dooray Plus distribution (Windows/Python 3)."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
ORIGIN = 'https://github.com/neokyaru/dooooray-plus.git'
PROJECT = 'neokyaru/dooooray-plus'
ROOT_FILES = {'catalog.json', 'emoticon-index.json', '.gitignore', '.gitattributes', 'README.md', 'update-public.pem',
              '01-Register-New-Packs.cmd', '02-Validate.cmd', '03-Publish.cmd'}
EXTENSIONS = {'.png', '.gif', '.jpg', '.jpeg', '.webp'}
SECRET = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|glpat-[A-Za-z0-9_-]{20,}')

def git(root, *args, check=True):
    result = subprocess.run(['git', '-c', 'core.quotepath=false', *args], cwd=root,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise ValueError(result.stderr.decode('utf-8', errors='replace').strip() or 'Git operation failed')
    return result

def text_git(root, *args):
    return git(root, *args).stdout.decode('utf-8').strip()

def managed(name):
    parts = PurePosixPath(name).parts
    if not parts or any(p in {'.', '..', '.git', 'workspace', 'backups', '__pycache__'} for p in parts):
        return False
    if any(p.startswith('.env') or p.lower() in {'.ds_store', 'thumbs.db'} for p in parts):
        return False
    if Path(name).suffix.lower() in {'.key', '.pfx', '.p12', '.pem'}:
        return name == 'update-public.pem'
    return name in ROOT_FILES or parts[0] in {'packs', 'update', 'script'}

def regular(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative or re.search(r'[\x00-\x1f]', relative):
        raise ValueError('Invalid relative asset path')
    parts = relative.split('/')
    if any(p in {'', '.', '..'} for p in parts):
        raise ValueError('Asset path escapes the repository')
    result = root.joinpath(*parts)
    for candidate in [result, *result.parents]:
        if candidate.is_symlink() or getattr(candidate, 'is_junction', lambda: False)():
            raise ValueError('Linked asset path is not allowed')
        if candidate == root:
            break
    if not result.is_file() or not result.resolve().is_relative_to(root.resolve()):
        raise ValueError('Missing regular asset: ' + relative)
    return result

def load_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def validate_assets(root):
    catalog = load_json(regular(root, 'catalog.json'))
    entries = catalog.get('packs') if isinstance(catalog, dict) else None
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError('Catalog must contain 1 to 100 packs')
    seen = set(); total = 0
    def image(relative):
        file = regular(root, relative)
        if not relative.startswith('packs/') or file.suffix.lower() not in EXTENSIONS or file.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('Image must be PNG/GIF/JPEG/WebP up to 8 MiB: ' + relative)
        with file.open('rb') as stream:
            head = stream.read(12)
        if not (head.startswith(b'\x89PNG\r\n\x1a\n') or head[:6] in {b'GIF87a', b'GIF89a'} or head.startswith(b'\xff\xd8\xff') or head[:4] == b'RIFF' and head[8:12] == b'WEBP'):
            raise ValueError('Invalid image signature: ' + relative)
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id']:
            raise ValueError('Invalid catalog pack')
        identity = unicodedata.normalize('NFC', entry['id'])
        if identity in seen:
            raise ValueError('Duplicate catalog pack ID')
        seen.add(identity)
        name = entry.get('manifest')
        if not isinstance(name, str) or not name.startswith('packs/') or not name.endswith('.json'):
            raise ValueError('Invalid pack manifest path')
        manifest = load_json(regular(root, name)); items = manifest.get('items')
        if not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise ValueError('A pack must contain 1 to 500 images: ' + name)
        base = PurePosixPath(name).parent.as_posix(); ids = set()
        def resolve(value):
            if not isinstance(value, str):
                raise ValueError('Invalid image filename')
            return value if value.startswith('packs/') else base + '/' + value
        for item in items:
            item = {'file': item} if isinstance(item, str) else item
            if not isinstance(item, dict):
                raise ValueError('Invalid pack item')
            identity = str(item.get('id', len(ids) + 1))
            if identity in ids:
                raise ValueError('Duplicate pack item ID')
            ids.add(identity); image(resolve(item.get('file', item.get('path')))); total += 1
        if manifest.get('icon'):
            image(resolve(manifest['icon']))
    return len(entries), total

def validate_index(root, required=True):
    path = root / 'emoticon-index.json'
    if not path.exists() and not required:
        return None
    value = load_json(regular(root, 'emoticon-index.json'))
    if set(value) != {'schema', 'repository', 'ref'} or value['schema'] != 1 or value['repository'] != PROJECT or not isinstance(value['ref'], str) or not re.fullmatch(r'[0-9a-f]{40}', value['ref']):
        raise ValueError('Invalid immutable emoticon index')
    git(root, 'cat-file', '-e', value['ref'] + '^{commit}')
    return value['ref']

def inspect_paths(root, names):
    for name in names:
        if not managed(name):
            raise ValueError('Refusing unmanaged/staged path: ' + name)
        file = root / name
        if file.exists():
            file = regular(root, name)
            if file.stat().st_size > 100 * 1024 * 1024:
                raise ValueError('GitHub file exceeds 100 MiB: ' + name)
            if file.suffix.lower() in {'.py', '.mjs', '.js', '.ps1', '.cmd', '.sh', '.md', '.json', '.txt', '.yml', '.yaml', '.pem'} and file.stat().st_size <= 2 * 1024 * 1024:
                if SECRET.search(file.read_bytes()):
                    raise ValueError('Potential private credential in: ' + name)

def validate(root):
    if Path(text_git(root, 'rev-parse', '--show-toplevel')).resolve() != root.resolve() or text_git(root, 'symbolic-ref', '--short', 'HEAD') != 'main':
        raise ValueError('Use the main branch of this distribution checkout')
    if text_git(root, 'remote', 'get-url', 'origin') != ORIGIN or text_git(root, 'remote', 'get-url', '--push', 'origin') != ORIGIN:
        raise ValueError('Origin must be exactly ' + ORIGIN)
    validate_index(root, required=False)
    packs, items = validate_assets(root)
    staged = git(root, 'diff', '--cached', '--name-only', '-z').stdout.decode('utf-8').split('\0')
    inspect_paths(root, [name for name in staged if name])
    node = shutil.which('node')
    if not node:
        raise ValueError('Node.js is required to verify signed update files before publishing')
    subprocess.run([node, str(root / 'script' / 'verify-update.mjs'), str(root)], check=True)
    print(f'Validated {packs} packs / {items} images and signed updates.', flush=True)

def publish(root, message, no_push=False):
    validate(root)
    paths = [name for name in sorted(ROOT_FILES) if (root / name).exists()] + ['packs', 'update', 'script']
    git(root, 'add', '--all', '--', *paths)
    names = git(root, 'diff', '--cached', '--name-only', '-z').stdout.decode('utf-8').split('\0')
    inspect_paths(root, [name for name in names if name])
    if git(root, 'diff', '--cached', '--quiet', check=False).returncode:
        git(root, 'commit', '-m', message)
    old_ref = validate_index(root, required=False)
    # Prove equality of the committed asset trees. Program/script-only commits
    # never invalidate cached emoticons, including messages from the old host.
    changed = old_ref is None or git(root, 'diff', '--quiet', old_ref, 'HEAD', '--', 'catalog.json', 'packs', check=False).returncode != 0
    if changed:
        ref = text_git(root, 'rev-parse', 'HEAD')
        index = {'schema': 1, 'repository': PROJECT, 'ref': ref}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=root,
                                             prefix='.emoticon-index-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(index, indent=2) + '\n')
            os.replace(temporary, root / 'emoticon-index.json')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        git(root, 'add', '--', 'emoticon-index.json')
        git(root, 'commit', '-m', 'Pin immutable emoticon assets ' + ref[:12])
    validate_index(root)
    if no_push:
        print('Local preparation complete. No network push performed.'); return
    # Git Credential Manager prompts the user if authentication is needed.
    # No token is read by this script, written to files, or supplied as a URL.
    subprocess.run(['git', 'push', '--set-upstream', 'origin', 'main'], cwd=root, check=True)
    print('Published to ' + ORIGIN)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'publish'])
    parser.add_argument('--message', default='Update Dooray Plus distribution')
    parser.add_argument('--no-push', action='store_true', help='Prepare local commits only')
    args = parser.parse_args()
    try:
        if args.command == 'validate':
            validate(ROOT); validate_index(ROOT)
        else:
            publish(ROOT, args.message, args.no_push)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('ERROR: ' + str(error), file=sys.stderr); return 1

if __name__ == '__main__':
    sys.exit(main())
