#!/usr/bin/env python3
"""카탈로그에 없거나 pack.json이 없는 팩만 생성·등록합니다."""

import argparse
import sys

sys.dont_write_bytecode = True
from update_pack import normalized, pack_folders, read_catalog, repository_root, update_folders


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        root = repository_root()
        catalog = read_catalog(root)
        registered = {normalized(entry['id']) for entry in catalog['packs']}
        folders = [folder for pack_id, folder in pack_folders(root).items()
                   if pack_id not in registered or not (folder / 'pack.json').exists()]
        if not folders:
            print('새로 등록하거나 생성할 팩이 없습니다.')
            return 0
        update_folders(root, folders, catalog)
        print(f'완료: {len(folders)}개 팩을 처리했습니다.')
    except (OSError, ValueError) as error:
        print(f'오류: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
