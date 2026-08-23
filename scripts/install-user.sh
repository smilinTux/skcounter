#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
install_prefix=${SKCOUNTER_PREFIX:-"$HOME/.local"}

node -e 'const major = Number(process.versions.node.split(".")[0]); if (major < 20) process.exit(1)'
npm ci --ignore-scripts --prefix "$repo_root"
npm test --prefix "$repo_root"
npm install --global --ignore-scripts --prefix "$install_prefix" "$repo_root"
"$install_prefix/bin/skcounter" doctor
