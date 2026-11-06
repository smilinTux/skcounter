#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  printf '%s\n' "Usage: provision-collector-tls.sh BIND_IP TLS_DIRECTORY [EXTRA_IP...]" >&2
  exit 2
fi

bind_ip=$1
tls_dir=$2
shift 2
san="IP:${bind_ip}"
for ip in "$bind_ip" "$@"; do
  case "$ip" in
    *[!0-9.]*|'')
      printf '%s\n' "BIND_IP and EXTRA_IP must be IPv4 addresses" >&2
      exit 2
      ;;
  esac
done
for ip in "$@"; do san="${san},IP:${ip}"; done

umask 077
mkdir -p "$tls_dir"
chmod 700 "$tls_dir"
key_file="$tls_dir/collector.key"
cert_file="$tls_dir/collector.crt"

# Reissue when a wanted address is missing from the certificate (the certificate
# is its own CA, so callers must redistribute it to every edge afterwards).
need_cert=0
if [ ! -s "$key_file" ] || [ ! -s "$cert_file" ]; then
  need_cert=1
else
  have=$(openssl x509 -in "$cert_file" -noout -ext subjectAltName 2>/dev/null || true)
  for ip in "$bind_ip" "$@"; do
    case "$have" in *"IP Address:${ip}"*) ;; *) need_cert=1 ;; esac
  done
fi
if [ "$need_cert" = 1 ]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
    -subj "/CN=skcounter-collector" \
    -addext "subjectAltName=${san}" \
    -keyout "$key_file" -out "$cert_file" >/dev/null 2>&1
fi
chmod 600 "$key_file" "$cert_file"
openssl x509 -in "$cert_file" -noout -checkend 86400 >/dev/null
printf 'certificate=%s\n' "$cert_file"
