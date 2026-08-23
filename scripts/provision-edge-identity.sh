#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 5 ]; then
  printf '%s\n' "Usage: provision-edge-identity.sh CAPAUTH_HOME GNUPGHOME NODE_ID PRINCIPAL_ID PUBLIC_KEY_OUTPUT" >&2
  exit 2
fi

capauth_home=$1
gnupg_home=$2
node_id=$3
principal_id=$4
public_key_output=$5
subject="skcounter:${node_id}:${principal_id}"

umask 077
mkdir -p "$capauth_home/identity" "$capauth_home/security" "$gnupg_home" "$(dirname -- "$public_key_output")"
chmod 700 "$capauth_home" "$capauth_home/identity" "$capauth_home/security" "$gnupg_home"

fingerprint=$(gpg --homedir "$gnupg_home" --batch --with-colons --list-secret-keys 2>/dev/null | awk -F: '$1 == "fpr" {print $10; exit}')
if [ -z "$fingerprint" ]; then
  gpg --homedir "$gnupg_home" --batch --pinentry-mode loopback --passphrase '' \
    --quick-generate-key "SKCounter ${node_id} ${principal_id} <skcounter@${node_id}>" ed25519 sign 1y >/dev/null 2>&1
  fingerprint=$(gpg --homedir "$gnupg_home" --batch --with-colons --list-secret-keys | awk -F: '$1 == "fpr" {print $10; exit}')
fi

if ! printf '%s' "$fingerprint" | grep -Eq '^[A-F0-9]{40}$'; then
  printf '%s\n' "Unable to resolve the SKCounter service identity fingerprint" >&2
  exit 1
fi

identity_tmp="$capauth_home/identity/.identity.json.tmp"
printf '{"fingerprint":"%s","subject":"%s","purpose":"skcounter.report.submit"}\n' \
  "$fingerprint" "$subject" > "$identity_tmp"
chmod 600 "$identity_tmp"
mv -f "$identity_tmp" "$capauth_home/identity/identity.json"
gpg --homedir "$gnupg_home" --batch --armor --export "$fingerprint" > "$public_key_output"
chmod 600 "$public_key_output"

printf 'fingerprint=%s\nsubject=%s\n' "$fingerprint" "$subject"
