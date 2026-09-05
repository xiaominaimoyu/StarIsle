#!/usr/bin/env sh
exec python3 "$(dirname "$0")/backup_restore.py" restore "$@"
