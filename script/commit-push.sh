#!/bin/bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -gt 1 ]]; then echo "Usage: $0 [message]" >&2; exit 1; fi
exec python3 "$script_dir/publish.py" publish --message "${1:-Update Dooray Plus distribution}"
