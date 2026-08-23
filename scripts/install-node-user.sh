#!/usr/bin/env bash
set -euo pipefail

node_version=${1:-22.23.2}
case "$node_version" in
  *[!0-9.]*|'')
    printf '%s\n' "Node.js version must contain only digits and dots" >&2
    exit 2
    ;;
esac

case "$(uname -m)" in
  x86_64) node_arch=x64 ;;
  aarch64|arm64) node_arch=arm64 ;;
  *)
    printf 'Unsupported architecture: %s\n' "$(uname -m)" >&2
    exit 2
    ;;
esac

filename="node-v${node_version}-linux-${node_arch}.tar.xz"
base_url="https://nodejs.org/dist/v${node_version}"
install_root=${SKCOUNTER_NODE_ROOT:-"$HOME/.local/lib/node-v${node_version}-linux-${node_arch}"}
bin_root=${SKCOUNTER_PREFIX:-"$HOME/.local"}/bin
download_root=$(mktemp -d)
trap 'find "$download_root" -type f -delete 2>/dev/null || true; find "$download_root" -depth -type d -empty -delete 2>/dev/null || true' EXIT

if [ ! -x "$install_root/bin/node" ]; then
  curl --fail --silent --show-error --location "$base_url/SHASUMS256.txt" --output "$download_root/SHASUMS256.txt"
  curl --fail --silent --show-error --location "$base_url/$filename" --output "$download_root/$filename"
  expected=$(awk -v name="$filename" '$2 == name {print $1}' "$download_root/SHASUMS256.txt")
  if [ -z "$expected" ]; then
    printf '%s\n' "Release checksum does not list the selected archive" >&2
    exit 1
  fi
  actual=$(sha256sum "$download_root/$filename" | awk '{print $1}')
  if [ "$actual" != "$expected" ]; then
    printf '%s\n' "Node.js archive checksum mismatch" >&2
    exit 1
  fi
  mkdir -p "$(dirname -- "$install_root")"
  tar -xJf "$download_root/$filename" -C "$(dirname -- "$install_root")"
fi

mkdir -p "$bin_root"
for command in node npm npx corepack; do
  ln -sfn "$install_root/bin/$command" "$bin_root/$command"
done

installed=$($bin_root/node --version)
if [ "$installed" != "v$node_version" ]; then
  printf 'Installed Node.js version mismatch: %s\n' "$installed" >&2
  exit 1
fi
printf 'Node.js %s installed for %s at %s\n' "$installed" "$USER" "$install_root"
