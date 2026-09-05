#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
venv_python="$script_dir/../apps/api/.venv/bin/python"

if [ -x "$venv_python" ]; then
  exec "$venv_python" -B "$script_dir/recovery_drill.py" "$@"
fi
exec python3 -B "$script_dir/recovery_drill.py" "$@"
