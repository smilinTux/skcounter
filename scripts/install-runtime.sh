#!/usr/bin/env bash
set -euo pipefail

role=${1:-}
if [ "$role" != "edge" ] && [ "$role" != "collector" ] && [ "$role" != "all" ]; then
  printf '%s\n' "Usage: install-runtime.sh edge|collector|all" >&2
  exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
runtime_root=${SKCOUNTER_RUNTIME_ROOT:-"$HOME/.local/lib/skcounter"}
unit_root=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user

install -d -m 0700 "$runtime_root" "$unit_root"
install -d -m 0700 "$runtime_root/src" "$runtime_root/services" "$runtime_root/edge"
install -m 0644 "$repo_root"/src/*.mjs "$runtime_root/src/"
install -m 0755 "$repo_root/services/collector.mjs" "$runtime_root/services/collector.mjs"
install -m 0755 "$repo_root/services/capauth_verify.py" "$runtime_root/services/capauth_verify.py"
install -m 0755 "$repo_root/edge/skcounter_edge.py" "$runtime_root/edge/skcounter_edge.py"
install -m 0755 "$repo_root/edge/skcounter_schedule.py" "$runtime_root/edge/skcounter_schedule.py"
install -m 0755 "$repo_root/edge/run-edge.sh" "$runtime_root/edge/run-edge.sh"
install -m 0644 "$repo_root/edge/__init__.py" "$runtime_root/edge/__init__.py"

if [ "$role" = "collector" ] || [ "$role" = "all" ]; then
  install -m 0644 "$repo_root/deploy/systemd/skcounter-collector.service" "$unit_root/skcounter-collector.service"
fi
if [ "$role" = "edge" ] || [ "$role" = "all" ]; then
  install -m 0644 "$repo_root/deploy/systemd/skcounter-edge.service" "$unit_root/skcounter-edge.service"
  install -m 0644 "$repo_root/deploy/systemd/skcounter-edge.timer" "$unit_root/skcounter-edge.timer"
fi

systemctl --user daemon-reload
printf 'SKCounter %s runtime installed at %s. No service or timer was enabled.\n' "$role" "$runtime_root"
