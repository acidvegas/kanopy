#!/usr/bin/env bash
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# hook.sh

# Print the kanopy project and open tasks for a directory (default $PWD); Claude Code's SessionStart hook adds the output to the session

dir="$(cd "$(dirname "$0")" && pwd)"
key="$(sed -n 's/^KANOPY_KEY=//p' "$dir/.env" 2>/dev/null)"
host="$(sed -n 's/^KANOPY_HOST=//p' "$dir/.env" 2>/dev/null)"
port="$(sed -n 's/^KANOPY_PORT=//p' "$dir/.env" 2>/dev/null)"

[ -n "$key" ] || exit 0

curl -sf -m 3 -G -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36' -H "X-Key: $key" --data-urlencode "cwd=${1:-$PWD}" "http://${host:-127.0.0.1}:${port:-8000}/api/context" || true
