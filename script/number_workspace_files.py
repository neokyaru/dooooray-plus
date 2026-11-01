#!/usr/bin/env python3
"""Windows-compatible numbering tool. --dry-run previews without renaming."""
import argparse
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata

default_root = str(Path(__file__).resolve().parent.parent / 'workspace')
parser = argparse.ArgumentParser(
    description='workspace의 각 폴더에서 파일명 순서대로 이름을 01, 02, 03으로 바꿉니다. 확장자, 파일 내용과 폴더명은 유지합니다.')
parser.add_argument('folder', nargs='?', default=default_root, help='대상 폴더 (기본: workspace)')
parser.add_argument('--dry-run', action='store_true', help='실제 변경 없이 변경 목록만 표시')
args = parser.parse_args()


def linked(path):
    attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    return path.is_symlink() or bool(attributes & 0x400)


def natural_key(name):
    text = unicodedata.normalize('NFC', name).casefold()
    parts = tuple((1, int(part)) if part.isdecimal() else (0, part)
                  for part in re.split(r'(\d+)', text))
    return parts, unicodedata.normalize('NFC', name), name


def new_name(path, index):
    # 기존 이름은 버리고 순번과 원래 확장자만 남깁니다.
    return f'{index:02d}{path.suffix}'


def move_without_overwrite(source, target):
    # 같은 폴더 안에서 원본 inode를 유지하고, 기존 파일을 덮어쓰지 않습니다.
    os.link(source, target, follow_symlinks=False)
    source.unlink()


def rename_folder(changes):
    staged = []
    completed = []
    try:
        # 먼저 임시 이름으로 옮겨 01→02 / 02→01 같은 이름 충돌을 피합니다.
        for source, target in changes:
            descriptor, temporary_name = tempfile.mkstemp(prefix='.number-files-', dir=source.parent)
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                os.replace(source, temporary)
            except BaseException:
                temporary.unlink()
                raise
            staged.append((source, target, temporary))
        for source, target, temporary in staged:
            move_without_overwrite(temporary, target)
            completed.append((source, target, temporary))
    except BaseException:
        # 처리 중 오류가 나면 현재 폴더의 원래 이름으로 되돌립니다.
        for source, target, temporary in reversed(completed):
            move_without_overwrite(target, temporary)
        for source, target, temporary in reversed(staged):
            if temporary.exists():
                move_without_overwrite(temporary, source)
        raise


try:
    root = Path(args.folder).expanduser()
    if linked(root) or not root.is_dir():
        raise ValueError(f'실제 폴더를 지정해 주세요: {root}')
    root = root.resolve()
    plans = []
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        folder = Path(directory)
        subdirectories[:] = sorted(
            (name for name in subdirectories
             if not name.startswith('.') and not linked(folder / name)),
            key=natural_key)
        files = sorted(
            (folder / name for name in filenames
             if not name.startswith('.') and not linked(folder / name)
             and (folder / name).is_file()),
            key=lambda path: natural_key(path.name))
        changes = [(path, path.with_name(new_name(path, index)))
                   for index, path in enumerate(files, 1)]
        changes = [(source, target) for source, target in changes if source != target]
        sources = {source for source, _ in changes}
        for source, target in changes:
            if os.path.lexists(target) and target not in sources:
                raise ValueError(f'이름이 겹치는 파일 또는 폴더가 있습니다: {target}')
        if changes:
            plans.append(changes)

    count = sum(len(changes) for changes in plans)
    for changes in plans:
        for source, target in changes:
            print(f'{source.relative_to(root)} → {target.name}', flush=True)
        if not args.dry_run:
            rename_folder(changes)
    action = '변경 예정' if args.dry_run else '변경 완료'
    print(f'{action}: {len(plans)}개 폴더, {count}개 파일')
except (OSError, ValueError) as error:
    print(f'오류: {error}', file=sys.stderr)
    sys.exit(1)
