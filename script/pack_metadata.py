"""Shared public pack metadata rules and rollback-safe JSON writes.

Passwords are public usage preferences, not authentication or encryption.
This module never changes image files, commits, publishes or installs anything.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import uuid

PASSWORD_VERSION = '3.8.34'
MAX_PASSWORD = 128
VERSION = re.compile(r'(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\Z')


def normalized(value):
    return unicodedata.normalize('NFC', value)


def linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def version_parts(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError('minVersion은 안정 버전 형식이어야 합니다 (예: 3.8.34).')
    return tuple(int(part) for part in value.split('.'))


def validate_password(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= MAX_PASSWORD
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError('암호는 제어문자 없이 1~128개의 Unicode 문자여야 합니다.')
    return value


def protected(manifest):
    if not isinstance(manifest, dict):
        raise ValueError('pack manifest는 JSON 객체여야 합니다.')
    minimum = version_parts(manifest['minVersion']) if 'minVersion' in manifest else None
    if 'password' not in manifest:
        return False
    validate_password(manifest['password'])
    if minimum is None or minimum < version_parts(PASSWORD_VERSION):
        raise ValueError('암호가 있는 팩의 minVersion은 3.8.34 이상이어야 합니다.')
    return True


def catalog_entries(catalog, minimum=0):
    if (not isinstance(catalog, dict) or type(catalog.get('schemaVersion')) is not int
            or catalog['schemaVersion'] != 1):
        raise ValueError('catalog.json의 schemaVersion은 1이어야 합니다.')
    public = catalog.get('packs')
    locked = catalog.get('lockedPacks', [])
    if not isinstance(public, list) or not isinstance(locked, list):
        raise ValueError('catalog.json의 packs와 lockedPacks는 배열이어야 합니다.')
    entries = public + locked
    if not minimum <= len(entries) <= 100:
        raise ValueError('packs와 lockedPacks의 합계는 100개 이하여야 합니다.')
    ids, manifests = set(), set()
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get('id'), str)
                or not entry['id'] or not isinstance(entry.get('manifest'), str)):
            raise ValueError('catalog의 팩 항목에는 id와 manifest가 필요합니다.')
        identity = normalized(entry['id'])
        if identity in ids:
            raise ValueError('catalog에 중복된 팩 ID가 있습니다: ' + identity)
        ids.add(identity)
        manifest = entry['manifest']
        parts = manifest.split('/')
        if (not manifest.startswith('packs/') or not manifest.endswith('.json')
                or '\\' in manifest or ':' in manifest
                or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in manifest)
                or any(p in {'', '.', '..'} for p in parts)):
            raise ValueError('팩 manifest 경로가 올바르지 않습니다.')
        canonical = normalized(manifest)
        if canonical in manifests:
            raise ValueError('catalog의 manifest 경로가 중복됩니다.')
        manifests.add(canonical)
    return entries


def metadata_path(root, relative, required=True):
    root = Path(root)
    if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative
            or any(ord(c) < 32 for c in relative)
            or any(p in {'', '.', '..'} for p in relative.split('/'))):
        raise ValueError('저장소 내부의 상대 경로가 필요합니다.')
    path = root.joinpath(*relative.split('/'))
    for candidate in [path, *path.parents]:
        if os.path.lexists(candidate) and linked(candidate):
            raise ValueError('연결된 metadata 경로는 처리할 수 없습니다.')
        if candidate == root:
            break
    else:
        raise ValueError('저장소 밖 경로는 처리할 수 없습니다.')
    if not path.parent.is_dir() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('metadata 경로가 저장소 밖을 가리킵니다.')
    if path.exists() and not path.is_file() or required and not path.is_file():
        raise ValueError('일반 metadata 파일이 필요합니다: ' + relative)
    return path


def read_manifest(root, relative):
    return json.loads(metadata_path(root, relative).read_text(encoding='utf-8-sig'))


def reconcile_catalog(root, catalog, overrides=None, additions=()):
    """Keep existing entry fields/order; move only entries whose lock state changed."""
    overrides = overrides or {}
    result = deepcopy(catalog)
    entries = catalog_entries(result)
    by_id = {normalized(e['id']): e for e in entries}
    for entry in additions:
        identity = normalized(entry['id'])
        if identity in by_id:
            by_id[identity].update(deepcopy(entry))
        else:
            by_id[identity] = deepcopy(entry)
            entries.append(by_id[identity])
    # Validate additions and total before reading or writing any destination.
    probe = dict(result, packs=entries, lockedPacks=[])
    catalog_entries(probe)
    states = {}
    for entry in entries:
        manifest = overrides.get(entry['manifest'])
        if manifest is None:
            manifest = read_manifest(root, entry['manifest'])
        states[normalized(entry['id'])] = protected(manifest)
    public, locked = [], []
    for source in [result['packs'], result.get('lockedPacks', [])]:
        for entry in source:
            (locked if states[normalized(entry['id'])] else public).append(entry)
    original = {normalized(e['id']) for e in result['packs'] + result.get('lockedPacks', [])}
    for entry in entries:
        if normalized(entry['id']) not in original:
            (locked if states[normalized(entry['id'])] else public).append(entry)
    result['packs'] = public
    if locked or 'lockedPacks' in result:
        result['lockedPacks'] = locked
    catalog_entries(result)
    return result


def _directory(root, relative):
    current = Path(root)
    if linked(current):
        raise ValueError('연결된 저장소는 처리할 수 없습니다.')
    for part in relative.split('/'):
        current = current / part
        if os.path.lexists(current):
            if linked(current) or not current.is_dir():
                raise ValueError('백업 경로는 일반 폴더여야 합니다.')
        else:
            current.mkdir()
    return current


def write_changes(root, changes, label='pack-metadata', replace=None):
    """Back up originals; stage every JSON; restore exact bytes on a write failure."""
    root = Path(root)
    if not isinstance(label, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', label):
        raise ValueError('Invalid backup category')
    replace = replace or os.replace
    prepared = []
    for relative, value in changes.items():
        path = metadata_path(root, relative, required=False)
        data = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        before = path.read_bytes() if path.exists() else None
        if before == data:
            continue
        prepared.append({'relative': relative, 'path': path, 'data': data, 'before': before,
                         'mode': path.stat().st_mode & 0o777 if path.exists() else 0o644})
    if not prepared:
        return None
    # Hiding from legacy catalogs takes effect before adding a new password.
    prepared.sort(key=lambda row: row['relative'] != 'catalog.json')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f') + '-' + uuid.uuid4().hex[:8]
    backup = _directory(root, 'script/backups/' + label + '/' + stamp)
    temporary, committed = [], []
    try:
        for row in prepared:
            if row['before'] is not None:
                saved = backup.joinpath(*row['relative'].split('/'))
                saved.parent.mkdir(parents=True, exist_ok=True)
                with saved.open('xb') as stream:
                    stream.write(row['before'])
            descriptor, name = tempfile.mkstemp(prefix='.' + row['path'].name + '.',
                                                suffix='.tmp', dir=row['path'].parent)
            stage = Path(name)
            temporary.append(stage)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(row['data'])
                stream.flush()
                os.fsync(stream.fileno())
            stage.chmod(row['mode'])
            row['stage'] = stage
        for row in prepared:
            metadata_path(root, row['relative'], required=False)
            now = row['path'].read_bytes() if row['path'].exists() else None
            if now != row['before']:
                raise ValueError('작업 중 metadata가 변경되어 저장하지 않았습니다.')
        for row in prepared:
            metadata_path(root, row['relative'], required=False)
            current = row['path'].read_bytes() if row['path'].exists() else None
            if current != row['before']:
                raise ValueError('작업 중 metadata가 변경되어 저장하지 않았습니다.')
            replace(row['stage'], row['path'])
            committed.append(row)
    except BaseException as error:
        restore_errors = []
        for row in reversed(committed):
            try:
                metadata_path(root, row['relative'], required=False)
                if row['before'] is None:
                    row['path'].unlink()
                else:
                    descriptor, name = tempfile.mkstemp(prefix='.restore-', suffix='.tmp', dir=row['path'].parent)
                    restored = Path(name)
                    temporary.append(restored)
                    with os.fdopen(descriptor, 'wb') as stream:
                        stream.write(row['before'])
                    restored.chmod(row['mode'])
                    os.replace(restored, row['path'])
            except BaseException:
                restore_errors.append(row['relative'])
        if restore_errors:
            raise OSError('metadata 복원 실패. 원본 백업을 확인해 주세요: ' + str(backup)) from error
        raise
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return backup
