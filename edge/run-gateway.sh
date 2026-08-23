#!/usr/bin/env bash
set -euo pipefail

schedule="$HOME/.local/lib/skcounter/edge/skcounter_schedule.py"
config="$HOME/.config/skcounter/gateway.json"

for python_bin in "$HOME/.skenv/bin/python3" /usr/bin/python3; do
  if [ -x "$python_bin" ] && "$python_bin" -c 'from capauth.tokens import mint_audience_token' >/dev/null 2>&1; then
    exec "$python_bin" "$schedule" "$config"
  fi
done

printf '%s\n' "SKCounter gateway error: no Python runtime with CapAuth is available" >&2
exit 69
