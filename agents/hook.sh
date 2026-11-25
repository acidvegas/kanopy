#!/usr/bin/env bash
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# agents/hook.sh

# Print the kanopy project and open tasks for a directory (default $PWD); Claude Code's SessionStart hook adds the output to the session

dir="$(cd "$(dirname "$0")/.." && pwd)"
key="$(sed -n 's/^KANOPY_KEY=//p' "$dir/.env" 2>/dev/null)"
host="$(sed -n 's/^KANOPY_HOST=//p' "$dir/.env" 2>/dev/null)"
port="$(sed -n 's/^KANOPY_PORT=//p' "$dir/.env" 2>/dev/null)"
url="$(sed -n 's/^KANOPY_URL=//p' "$dir/.env" 2>/dev/null)" # set when kanopy runs elsewhere or behind a proxy

[ -n "$key" ] || exit 0

cwd="${1:-$PWD}"
case "$cwd" in "$HOME"|"$HOME"/*) cwd="~${cwd#"$HOME"}" ;; esac # as ~/..., so a kanopy on another machine matches ~/ project folders

curl -sf -m 3 -G -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36' -H "X-Key: $key" --data-urlencode "cwd=$cwd" "${url:-http://${host:-127.0.0.1}:${port:-8000}}/api/context" || true
