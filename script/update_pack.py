#!/usr/bin/env python3
"""지정한 팩의 pack.json과 catalog.json 항목을 갱신합니다."""

import argparse
import json
import os
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import tempfile
import unicodedata


IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.gif', '.png', '.webp'}


def linked(path):
    attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    return path.is_symlink() or bool(attributes & 0x400)


def normalized(value):
    return unicodedata.normalize('NFC', value)


def repository_root():
    return Path(__file__).resolve().parent.parent


def read_json(path):
    if linked(path):
        raise ValueError('Linked manifest is not allowed: ' + str(path))
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ValueError(f'JSON 형식을 확인해 주세요: {path} ({error})') from error


def read_catalog(root):
    path = root / 'catalog.json'
    catalog = read_json(path) if path.exists() else {'schemaVersion': 1, 'packs': []}
    if not isinstance(catalog, dict) or catalog.get('schemaVersion') != 1:
        raise ValueError('catalog.json의 schemaVersion은 1이어야 합니다.')
    if not isinstance(catalog.get('packs'), list):
        raise ValueError('catalog.json의 packs는 배열이어야 합니다.')
    seen = set()
    for entry in catalog['packs']:
        if (not isinstance(entry, dict)
                or not isinstance(entry.get('id'), str) or not entry['id']
                or not isinstance(entry.get('manifest'), str)):
            raise ValueError('catalog.json의 팩 항목에는 id와 manifest가 필요합니다.')
        pack_id = normalized(entry['id'])
        if pack_id in seen:
            raise ValueError(f'카탈로그에 중복된 팩 ID가 있습니다: {pack_id}')
        seen.add(pack_id)
    return catalog


def pack_folders(root):
    packs = root / 'packs'
    if not packs.is_dir():
        raise ValueError(f'packs 폴더가 없습니다: {packs}')
    folders = {}
    for folder in sorted(packs.iterdir(), key=lambda path: normalized(path.name)):
        if linked(folder):
            raise ValueError('Linked pack folder is not allowed: ' + str(folder))
        if not folder.is_dir() or folder.name.startswith('.'):
            continue
        if folder.resolve().parent != packs.resolve():
            raise ValueError(f'packs 외부를 가리키는 폴더는 처리할 수 없습니다: {folder}')
        pack_id = normalized(folder.name)
        if pack_id in folders:
            raise ValueError(f'중복된 팩 폴더 이름입니다: {pack_id}')
        folders[pack_id] = folder
    return folders


def build_manifest(folder):
    images = sorted(
        (path for path in folder.iterdir()
         if path.is_file() and not path.name.startswith('.')
         and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: normalized(path.name),
    )
    if not images:
        raise ValueError(f'등록할 이미지가 없습니다: {folder.name}')
    if any(linked(image) for image in images):
        raise ValueError('Linked image is not allowed: ' + str(folder))
    items = []
    seen = set()
    for image in images:
        item_id = normalized(image.stem)
        if item_id in seen:
            raise ValueError(f'{folder.name}: 확장자를 뺀 파일명이 중복됩니다: {item_id}')
        seen.add(item_id)
        items.append({'id': item_id, 'label': item_id, 'file': image.name})
    path = folder / 'pack.json'
    manifest = read_json(path) if path.exists() else {}
    if not isinstance(manifest, dict):
        raise ValueError(f'pack.json은 JSON 객체여야 합니다: {folder.name}')
    version = manifest.get('version', 1)
    if type(version) is not int or version < 1:
        raise ValueError(f'version은 1 이상의 정수여야 합니다: {folder.name}')
    pack_id = normalized(folder.name)
    manifest.update(id=pack_id, name=pack_id, version=version,
                    icon=items[0]['file'], items=items)
    return manifest


def write_json(path, data):
    content = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if path.exists() and path.read_text(encoding='utf-8') == content:
        return False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=path.parent, prefix=f'.{path.name}.',
                                         suffix='.tmp', delete=False, newline='\n') as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return True


def update_folders(root, folders, catalog):
    # 전체 대상의 형식과 중복을 먼저 확인한 다음 파일을 씁니다.
    planned = [(folder, build_manifest(folder)) for folder in folders]
    entries = {normalized(entry['id']): dict(entry) for entry in catalog['packs']}
    for folder, manifest in planned:
        pack_id = manifest['id']
        entry = entries.setdefault(pack_id, {})
        entry.update(id=pack_id, manifest=folder.relative_to(root).as_posix() + '/pack.json')
    catalog['packs'] = [entries[key] for key in sorted(entries)]
    for folder, manifest in planned:
        path = folder / 'pack.json'
        existed = path.exists()
        changed = write_json(path, manifest)
        action = ('갱신' if existed else '생성') if changed else '변경 없음'
        print(f'{action}: {manifest["id"]} — 이미지 {len(manifest["items"])}개')
    changed = write_json(root / 'catalog.json', catalog)
    print(f'catalog.json: {"갱신" if changed else "변경 없음"} — 총 {len(entries)}개 팩')


def select_folder(root, value):
    folders = pack_folders(root)
    if normalized(value) in folders:
        return folders[normalized(value)]
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path if path.parts and path.parts[0] == 'packs' else root / 'packs' / path
    if not path.is_dir() or path.resolve().parent != (root / 'packs').resolve():
        raise ValueError(f'packs 바로 아래의 폴더를 지정해 주세요: {value}')
    pack_id = normalized(path.name)
    if pack_id not in folders:
        raise ValueError(f'팩 폴더를 찾을 수 없습니다: {value}')
    return folders[pack_id]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', help='팩 폴더 이름 또는 packs 아래 폴더 경로 (공백이 있으면 따옴표 사용)')
    args = parser.parse_args()
    try:
        root = repository_root()
        folder = select_folder(root, args.folder)
        update_folders(root, [folder], read_catalog(root))
    except (OSError, ValueError) as error:
        print(f'오류: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
