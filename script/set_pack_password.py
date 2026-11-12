#!/usr/bin/env python3
"""팩 사용 암호를 설정/해제합니다. 공개 평문 설정이며 인증·암호화가 아닙니다."""
import argparse
from copy import deepcopy
import getpass
import sys

sys.dont_write_bytecode = True
from pack_metadata import (PASSWORD_VERSION, catalog_entries, normalized, protected,
                           read_manifest, reconcile_catalog, validate_password,
                           version_parts, write_changes)
from update_pack import read_catalog, repository_root


def pack_records(root, catalog):
    records = []
    for entry in catalog_entries(catalog):
        manifest = read_manifest(root, entry['manifest'])
        locked = protected(manifest)
        records.append({'entry': entry, 'manifest': manifest, 'locked': locked})
    return sorted(records, key=lambda row: normalized(row['entry']['id']))


def parse_selection(value, count):
    value = value.strip()
    if value.casefold() in {'all', 'a', '*', '전체'}:
        return list(range(count))
    chosen = set()
    for part in value.split(','):
        part = part.strip()
        if not part:
            raise ValueError('선택 번호가 비어 있습니다.')
        pieces = [p.strip() for p in part.split('-')]
        if len(pieces) not in (1, 2) or any(not p.isascii() or not p.isdecimal() for p in pieces):
            raise ValueError('선택은 1,3-5 또는 all 형식으로 입력해 주세요.')
        start = int(pieces[0]); end = int(pieces[-1])
        if not 1 <= start <= end <= count:
            raise ValueError('선택 번호가 범위를 벗어났습니다.')
        chosen.update(range(start - 1, end))
    if not chosen:
        raise ValueError('하나 이상의 팩을 선택해 주세요.')
    return sorted(chosen)


def read_password(prompt):
    # Windows getpass reads console keystrokes even with redirected stdin.
    # Piped interactive sessions must consume their own stream, never wait on
    # another console or expose the supplied value in application output.
    return getpass.getpass(prompt) if sys.stdin.isatty() else input(prompt)


def apply_passwords(root, ids, password=None, clear=False, dry_run=False, replace=None):
    if clear and password is not None:
        raise ValueError('암호 설정과 해제를 동시에 할 수 없습니다.')
    if not clear:
        validate_password(password)
    catalog = read_catalog(root)
    records = pack_records(root, catalog)
    wanted = {normalized(value) for value in ids}
    known = {normalized(row['entry']['id']) for row in records}
    if not wanted or not wanted <= known:
        raise ValueError('등록된 팩을 하나 이상 선택해 주세요.')
    overrides = {}
    for row in records:
        if normalized(row['entry']['id']) not in wanted:
            continue
        manifest = deepcopy(row['manifest'])
        if clear:
            had_password = 'password' in manifest
            manifest.pop('password', None)
            if had_password and manifest.get('minVersion') == PASSWORD_VERSION:
                manifest.pop('minVersion')
        else:
            manifest['password'] = password
            if ('minVersion' not in manifest
                    or version_parts(manifest['minVersion']) < version_parts(PASSWORD_VERSION)):
                manifest['minVersion'] = PASSWORD_VERSION
        protected(manifest)
        overrides[row['entry']['manifest']] = manifest
    updated = reconcile_catalog(root, catalog, overrides)
    changes = {'catalog.json': updated, **overrides}
    if dry_run:
        return {'selected': len(wanted), 'dryRun': True, 'backup': None}
    backup = write_changes(root, changes, label='pack-password', replace=replace)
    return {'selected': len(wanted), 'dryRun': False, 'backup': str(backup) if backup else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    select = parser.add_mutually_exclusive_group()
    select.add_argument('--select', help='팩 번호: 1,3-5 또는 all')
    select.add_argument('--all', action='store_true', help='전체 팩 선택')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--password', help='공개 평문으로 저장할 암호 (셸 기록에도 남을 수 있음)')
    action.add_argument('--clear', action='store_true', help='암호 해제')
    parser.add_argument('--list', action='store_true', help='팩 번호와 설정 여부만 보기')
    parser.add_argument('--dry-run', action='store_true', help='설정 파일·백업을 바꾸지 않고 대상 확인')
    parser.add_argument('--yes', action='store_true', help='마지막 확인 생략')
    args = parser.parse_args(argv)
    try:
        root = repository_root()
        records = pack_records(root, read_catalog(root))
        print('팩 사용 암호 관리 — 공개 평문 설정이며 실제 인증/암호화가 아닙니다.')
        print('개인 계정의 비밀번호를 재사용하지 마세요. 원본 이미지는 바꾸지 않습니다.')
        if not records:
            print('등록된 팩이 없습니다. 먼저 새 팩 등록 도구를 실행해 주세요.')
            return 0 if args.list else 1
        for index, row in enumerate(records, 1):
            print(f'{index:3}. {row["entry"]["id"]} [{"암호 설정됨" if row["locked"] else "암호 없음"}]')
        if args.list:
            return 0
        value = 'all' if args.all else args.select
        if value is None:
            value = input('선택할 팩 번호 (예: 1,3-5 / 전체: all): ')
        selected = parse_selection(value, len(records))
        clear = args.clear
        password = args.password
        if not clear and password is None:
            choice = input('작업 선택 [1: 암호 설정 / 2: 암호 해제]: ').strip().casefold()
            if choice in {'2', 'clear', '해제'}:
                clear = True
            elif choice not in {'1', 'set', '설정'}:
                raise ValueError('1(설정) 또는 2(해제)를 선택해 주세요.')
            if not clear:
                password = read_password('새 암호 (공백 포함 그대로 저장): ')
                validate_password(password)
                if read_password('새 암호 확인: ') != password:
                    raise ValueError('두 암호가 일치하지 않습니다. 변경하지 않았습니다.')
        if not clear:
            validate_password(password)
        print(f'선택한 {len(selected)}개 팩: 암호 {"해제" if clear else "설정"}'
              + (' (미리보기)' if args.dry_run else ''))
        if not args.yes and not args.dry_run:
            if input('JSON 설정에 반영할까요? [y/N]: ').strip().casefold() not in {'y', 'yes', '예'}:
                print('취소했습니다. 변경하지 않았습니다.')
                return 0
        ids = [records[index]['entry']['id'] for index in selected]
        result = apply_passwords(root, ids, password, clear, args.dry_run)
        if result['dryRun']:
            print('미리보기 완료: 설정 파일과 백업을 바꾸지 않았습니다.')
        else:
            print(f'완료: {result["selected"]}개 팩의 암호 설정과 catalog를 동기화했습니다.')
            if result['backup']:
                print('원본 JSON 백업: ' + result['backup'])
        return 0
    except (OSError, ValueError, EOFError, KeyboardInterrupt) as error:
        print('오류: ' + (str(error) or '입력이 취소되었습니다. 변경하지 않았습니다.'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
