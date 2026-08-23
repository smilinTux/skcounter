#!/usr/bin/env bash
set -euo pipefail

install_prefix=${SKCOUNTER_PREFIX:-"$HOME/.local"}
npm uninstall --global --prefix "$install_prefix" @smilintux/skcounter

printf '%s\n' "SKCounter was removed from $install_prefix. Configuration and outbox state were preserved."
