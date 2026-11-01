#!/bin/sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$script_dir/update_new_packs.py" "$@"
