#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  printf '%s\n' "Usage: provision-collector-tls.sh BIND_IP TLS_DIRECTORY" >&2
  exit 2
fi

bind_ip=$1
tls_dir=$2
case "$bind_ip" in
  *[!0-9.]*|'')
    printf '%s\n' "BIND_IP must be an IPv4 address" >&2
    exit 2
    ;;
esac

umask 077
mkdir -p "$tls_dir"
chmod 700 "$tls_dir"
key_file="$tls_dir/collector.key"
cert_file="$tls_dir/collector.crt"

if [ ! -s "$key_file" ] || [ ! -s "$cert_file" ]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
    -subj "/CN=skcounter-collector" \
    -addext "subjectAltName=IP:${bind_ip}" \
    -keyout "$key_file" -out "$cert_file" >/dev/null 2>&1
fi
chmod 600 "$key_file" "$cert_file"
openssl x509 -in "$cert_file" -noout -checkend 86400 >/dev/null
printf 'certificate=%s\n' "$cert_file"
