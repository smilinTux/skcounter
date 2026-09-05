#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  printf '%s\n' "Usage: apply-fleet-cronjobs.sh NODE [NODE ...]" >&2
  exit 2
fi

command -v jq >/dev/null
command -v skcapstone >/dev/null

job=${SKCOUNTER_FLEET_JOB:-skcounter-edge}
case "$job" in
  skcounter-edge)
    lane=harness_reported
    service=skcounter-edge.service
    ;;
  skcounter-gateway)
    lane=gateway_observed
    service=skcounter-gateway.service
    ;;
  *)
    printf 'Unsupported SKCounter Fleet job: %s\n' "$job" >&2
    exit 2
    ;;
esac

temporary_dir=$(mktemp -d)
trap 'rm -rf -- "$temporary_dir"' EXIT

for node in "$@"; do
  case "$node" in
    *[!a-z0-9.-]*|'')
      printf 'Invalid Fleet node name: %s\n' "$node" >&2
      exit 2
      ;;
  esac
  object="$job-$node"
  jq -n \
    --arg name "$object" \
    --arg node "$node" \
    --arg lane "$lane" \
    --arg service "$service" \
    '{
      kind: "cronjob",
      name: $name,
      labels: {
        "app.kubernetes.io/name": "skcounter",
        "skcounter.lane": $lane,
        "skcounter.node": $node
      },
      spec: {
        command: ("systemctl --user start " + $service),
        schedule: "15m",
        enabled: true,
        nodeSelector: {"skcounter.node": $node}
      }
    }' >"$temporary_dir/$object.json"
  skcapstone fleet apply -f "$temporary_dir/$object.json"
done
