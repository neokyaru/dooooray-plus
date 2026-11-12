#!/usr/bin/env python3
"""Validate and publish the public Dooray Plus distribution (Windows/Python 3)."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / 'script') not in sys.path:
    sys.path.insert(0, str(ROOT / 'script'))
from pack_metadata import catalog_entries, protected
ORIGIN = 'https://github.com/neokyaru/dooooray-plus.git'
PROJECT = 'neokyaru/dooooray-plus'
ROOT_FILES = {'catalog.json', 'emoticon-index.json', '.gitignore', '.gitattributes', 'README.md', 'update-public.pem',
              '01-Register-New-Packs.cmd', '02-Validate.cmd', '03-Publish.cmd'}
EXTENSIONS = {'.png', '.gif', '.jpg', '.jpeg', '.webp'}
SECRET = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|glpat-[A-Za-z0-9_-]{20,}')
VERSION = r'(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})'
OLD_CAPSULE = re.compile(r'theme-(' + VERSION + r')\.zip')
OLD_INSTALLER = re.compile(r'Dooray(?:Plus|Theme)-(' + VERSION + r')-Setup\.exe')
OLD_RECOVERY = re.compile(r'DoorayPlus-(' + VERSION + r')-Manual-Recovery\.zip')


def update_stat(path, directory=False):
    """Never follow a link/reparse point while planning or removing update files."""
    result = path.lstat()
    if stat.S_ISLNK(result.st_mode) or getattr(result, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        raise ValueError('Linked update path is not allowed: ' + path.name)
    if not (stat.S_ISDIR(result.st_mode) if directory else stat.S_ISREG(result.st_mode)):
        raise ValueError('Unexpected update path type: ' + path.name)
    return result


def update_directory(path):
    for candidate in [path, *path.parents]:
        update_stat(candidate, directory=True)


def update_fingerprint(path):
    value = update_stat(path)
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, getattr(value, 'st_file_attributes', 0))


def update_snapshot(root):
    """The only deletion scope is the two immediate update directories."""
    directory = root / 'update'
    update_directory(directory)
    update_directory(directory / 'v2')
    files = {}
    for current in [directory, directory / 'v2']:
        for entry in current.iterdir():
            if current == directory and entry.name == 'v2':
                update_stat(entry, directory=True)
                continue
            relative = entry.relative_to(root).as_posix()
            files[relative] = update_fingerprint(entry)
    return files


def update_pruning_plan(root, before):
    """Called only after the public-key verifier has accepted both manifests."""
    after = update_snapshot(root)
    if before != after:
        raise ValueError('Update files changed during verification; publish again')
    patch = load_json(root / 'update' / 'v2' / 'latest.json')['manifest']
    installer = load_json(root / 'update' / 'v2' / 'installer.json')['manifest']
    version = patch['version']
    if not isinstance(version, str) or not re.fullmatch(VERSION, version):
        raise ValueError('Invalid current update version')
    archive, executable = patch['archive'], installer['file']
    if archive != 'theme-' + version + '.zip' or installer['version'] != version or executable != 'DoorayPlus-' + version + '-Setup.exe':
        raise ValueError('Current update metadata filenames disagree')
    keep = {'update/latest.json', 'update/' + archive, 'update/v2/latest.json',
            'update/v2/installer.json', 'update/v2/' + archive, 'update/v2/' + executable}
    if not keep.issubset(after):
        raise ValueError('Current update artifacts are incomplete')
    remove = []
    current_version = tuple(map(int, version.split('.')))
    for relative in sorted(set(after) - keep):
        name = PurePosixPath(relative).name
        match = OLD_CAPSULE.fullmatch(name) or OLD_INSTALLER.fullmatch(name)
        if match is None and PurePosixPath(relative).parent.as_posix() == 'update':
            match = OLD_RECOVERY.fullmatch(name)
        if match is None or tuple(map(int, match.group(1).split('.'))) >= current_version:
            raise ValueError('Unexpected/unreferenced update artifact; left untouched: ' + relative)
        remove.append(relative)
    # Detect changes while metadata was being read as well as during verification.
    if update_snapshot(root) != after:
        raise ValueError('Update files changed while planning; publish again')
    return {'version': version, 'remove': remove, 'files': after}


def prune_updates(root, plan):
    # Validate the entire plan before deleting any file. No recursive deletion,
    # Git history rewriting, pack/image pruning or guessed version is permitted.
    if update_snapshot(root) != plan['files']:
        raise ValueError('Update files changed after validation; nothing was removed')
    for relative in plan['remove']:
        file = root / relative
        update_directory(file.parent)
        if update_fingerprint(file) != plan['files'][relative]:
            raise ValueError('Update artifact changed before removal: ' + relative)
        file.unlink()
        print('Removed obsolete update: ' + relative, flush=True)

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
    entries = catalog_entries(catalog, minimum=1)
    locked_ids = {unicodedata.normalize('NFC', entry['id']) for entry in catalog.get('lockedPacks', [])}
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
        manifest = load_json(regular(root, name))
        if protected(manifest) != (identity in locked_ids):
            raise ValueError('Password manifest and public/locked catalog lane disagree: ' + name)
        items = manifest.get('items')
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
    before = update_snapshot(root)
    subprocess.run([node, str(root / 'script' / 'verify-update.mjs'), str(root)], check=True)
    plan = update_pruning_plan(root, before)
    print(f'Validated {packs} packs / {items} images and signed updates.', flush=True)
    print(f'Current update {plan["version"]}; {len(plan["remove"])} obsolete update files planned for removal.', flush=True)
    return plan

def publish(root, message, no_push=False, dry_run=False):
    plan = validate(root)
    paths = [name for name in sorted(ROOT_FILES) if (root / name).exists()] + ['packs', 'update', 'script']
    # Inspect everything git add will consider before any update file is removed.
    proposed = git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', *paths).stdout.decode('utf-8').split('\0')
    inspect_paths(root, [name for name in proposed if name])
    if dry_run:
        for name in plan['remove']:
            print('Would remove obsolete update: ' + name, flush=True)
        print('Dry run complete. No files, Git index, commits or network were changed.'); return
    prune_updates(root, plan)
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
    parser.add_argument('--dry-run', action='store_true', help='Validate and preview obsolete updates; change no files or Git state')
    args = parser.parse_args()
    try:
        if args.command == 'validate':
            validate(ROOT); validate_index(ROOT)
        else:
            publish(ROOT, args.message, args.no_push, args.dry_run)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('ERROR: ' + str(error), file=sys.stderr); return 1

if __name__ == '__main__':
    sys.exit(main())
