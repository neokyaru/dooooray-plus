#!/bin/sh
set -eu
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
unset PYLAUNCHER_ALLOW_INSTALL PYLAUNCHER_DRYRUN
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info.major != 3)' >/dev/null 2>&1; then
  exec python3 "$script_dir/set_pack_password.py" "$@"
elif command -v py >/dev/null 2>&1 && py -3 -c 'import sys; sys.exit(sys.version_info.major != 3)' >/dev/null 2>&1; then
  exec py -3 "$script_dir/set_pack_password.py" "$@"
elif command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(sys.version_info.major != 3)' >/dev/null 2>&1; then
  exec python "$script_dir/set_pack_password.py" "$@"
fi
printf '%s\n' 'Python 3 is required. No program or package was installed.' >&2
exit 1
